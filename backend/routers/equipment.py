"""
Equipment Router - Equipment CRUD and transfer endpoints
Scoping lives in dependencies.scope_equipment_query()
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from ..database import get_db
from ..dependencies import (
    get_current_active_user,
    get_daily_status,
    get_scoped_equipment_or_404,
    scope_equipment_query,
)
from ..enums import Capability
from .. import authz
from .. import clock
from .. import models
from .. import schemas

router = APIRouter(tags=["equipment"])


def _creation_group_id(db: Session, requested: Optional[int], creator: models.User) -> Optional[int]:
    """The group a newly created item will belong to. Decides nothing.

    Two ways in: derived from the creator's own membership, or named outright
    by the request. This function only says WHICH group; create_equipment gates
    whatever comes back, so the derived path and the attacker-chosen one answer
    to exactly the same rule and neither can be the soft one.

    Until H1-8 the override arm carried a check of its own, because the route
    had no gate at all and an unvalidated override would have let any
    authenticated user plant an item in any unit's group. The route has a gate
    now, asking `may(CREATE_EQUIPMENT, ...)` about the same group this returns,
    one line later -- so keeping the arm meant one predicate written twice, in
    two places, either of which could drift. That is DATA-H9's shape, and it
    survived mutation testing precisely because it was redundant: the override
    arm could be switched back to VIEW, or handed a role bypass, without a
    single test noticing, since the real gate caught both. Deleted rather than
    pinned. One decision, one place.

    What that costs is the more specific refusal -- "you cannot create in THAT
    group" instead of the generic one. Judged not worth a second copy of the
    rule: the verb is identical either way, so an operator learns nothing from
    the wording that the request body does not already tell them.

    No is_master short-circuit either. It was here because authz.may() reads
    grants and the algebra has no master arm, so removing it before H1-8 would
    have narrowed master rather than left them unchanged. Master now holds every
    capability on the root group -- seed_data and bootstrap_admin both issue
    those grants -- so the bypass had something to be replaced BY. H1-10 removed
    the last one, in dependencies.scope_equipment_query, and deleted is_master
    outright; visibility and authority now come from the same place.

    Returns None for a creator who is a member of nothing and named no
    override. Not an error here -- require() refuses it, since a group of None
    is reachable by no grant -- which is why the API can no longer produce an
    item belonging to no group, the state H1-6 described as visible to nobody.
    """
    if requested is not None:
        return requested
    return authz.primary_group_id(db, creator.id)

@router.get("/equipment/accessible", response_model=List[schemas.EquipmentResponse])
def get_accessible_equipment(
    query_str: Optional[str] = None,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get ALL equipment the user is allowed to see (Matrix Security).
    """
    q = scope_equipment_query(db.query(models.Equipment), current_user)

    # Optional text filter
    if query_str:
        search = f"%{query_str}%"
        q = q.join(models.CatalogItem).filter(
            (models.CatalogItem.name.ilike(search)) |
            (models.Equipment.status.ilike(search))
        )
        
    items = q.order_by(models.Equipment.id.asc()).all()
    
    results = []
    for item in items:
        compliance = get_daily_status(item.last_verified_at)
        results.append(schemas.EquipmentResponse(
            id=item.id,
            type=item.item_name,
            item_name=item.item_name,
            status=item.status,
            current_state_description=item.current_state_description,
            compliance_check=item.report_status,
            report_status=item.report_status,
            compliance_level=compliance,
            holder_user_id=item.holder_user_id,
            custom_location=item.custom_location,
            actual_location_id=item.actual_location_id,
            serial_number=item.serial_number,
            sensitivity=item.sensitivity  # DATA-H3-1
        ))
    return results

@router.post("/equipment/", response_model=schemas.EquipmentResponse)
def create_equipment(
    item: schemas.EquipmentCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Both resolved BEFORE the catalog block, because either can raise 403 and
    # the block below commits. Resolving them after would let a rejected
    # creation leave a permanent CatalogItem behind under an attacker-chosen
    # name -- a write performed by a request the route answered "denied".
    #
    # No resolver runs first here and there is no 404 to order against: nothing
    # is addressed by id, so nothing can be probed for. The group is an outcome
    # of the request rather than a handle to an existing row.
    group_id = _creation_group_id(db, item.group_id, current_user)
    authz.require(db, current_user.id, Capability.CREATE_EQUIPMENT, group_id)

    cat_item = db.query(models.CatalogItem).filter(models.CatalogItem.name == item.catalog_name).first()
    if not cat_item:
        cat_item = models.CatalogItem(name=item.catalog_name)
        db.add(cat_item)
        db.commit()

    # group_id is the whole of where this item belongs -- H1-11 dropped the
    # unit_hierarchy string that used to be written beside it, and made the
    # column NOT NULL. It cannot be None here: _creation_group_id may answer
    # None for a creator who is a member of nothing, and require() above
    # refuses a None group with a 403 rather than letting an item through that
    # belongs to no group and rises to no commander. That is H1-6's gap, closed
    # by the gate rather than by the constraint -- the constraint is the
    # backstop for everything that does not come through this route.
    new_item = models.Equipment(
        catalog_item_id=cat_item.id,
        serial_number=item.serial_number,
        group_id=group_id,
    )
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    
    return schemas.EquipmentResponse(
        id=new_item.id,
        type=new_item.item_name,
        item_name=new_item.item_name,
        status=new_item.status,
        current_state_description=new_item.current_state_description,
        compliance_check=new_item.report_status,
        report_status=new_item.report_status,
        compliance_level=new_item.compliance_level,
        serial_number=new_item.serial_number,
        holder_user_id=new_item.holder_user_id,
        custom_location=new_item.custom_location,
        actual_location_id=new_item.actual_location_id,
        sensitivity=new_item.sensitivity  # DATA-H3-1
    )

@router.post("/equipment/assign_owner/")
def assign_owner(
    req: schemas.AssignOwnerRequest, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Resolve, then decide -- and never the other way round. The item is looked
    # up inside the caller's own VIEW extent, so an id they cannot see answers
    # 404 and tells them nothing; only then is the grant question asked, so a
    # 403 reaches nobody who could not already list the row.
    #
    # This lookup used to be a bare filter on the raw id with an existence check
    # and no scope at all, which is why Company A's commander could reassign
    # Company B's item to anyone they liked. That is SEC-H6's shape at a site
    # SEC-H6 does not list, and it closes here as a consequence of the ordering
    # rather than as a separate fix.
    item = get_scoped_equipment_or_404(db, current_user, req.equipment_id)
    authz.require(db, current_user.id, Capability.TRANSFER, item.group_id)

    # The target is loaded rather than trusted. This route used to write
    # req.owner_id straight into two foreign keys with no lookup at all, so a
    # nonexistent id produced a dangling reference; reading the row closes
    # DATA-M2 FOR THIS ROUTE ONLY -- the ticket covers other sites and stays
    # open. It is also a precondition for the group derivation below, which
    # needs the user, not just their id.
    target = db.query(models.User).filter(models.User.id == req.owner_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target user not found")

    # The DESTINATION gate, identical to transfer_equipment's and for the same
    # reason -- this route re-derives the group from the new OWNER, so it moves
    # the item just as surely. Asking TRANSFER at the source only would let a
    # commander assign an item to someone in another unit and lose it.
    #
    # None means the target belongs to no group: the item stays where it is, so
    # there is no destination to authorise. The same-group clause that stood
    # beside this one is gone for the reason given in transfer_equipment -- it
    # could never change the answer.
    destination = authz.primary_group_id(db, target.id)
    if destination is not None:
        authz.require(db, current_user.id, Capability.TRANSFER, destination)

    item.owner_user_id = target.id
    item.holder_user_id = target.id
    # Ownership moves the item to the new owner's unit, so their commanders see
    # it and the previous owner's stop. A NULL group would be visible to no
    # commander at all, so a target who belongs to nowhere leaves it in place.
    if destination is not None:
        item.group_id = destination
    item.actual_location_id = None
    item.last_verified_at = clock.utcnow()
    item.custom_location = None
    
    db.commit()
    return {"status": "Ownership Assigned", "state": item.current_state_description}

@router.post("/equipment/transfer")
def transfer_equipment(
    req: schemas.TransferPossessionRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Transfer possession to a Person OR a Location (Strict XOR).
    """
    # XOR validation stays first, ahead of the resolver. It reads the request
    # body only, so it answers 400 without consulting any row, and a malformed
    # request is told so rather than told that an id it never supplied properly
    # does not exist. It discloses nothing: the answer is identical for every
    # caller and every id.
    if req.to_holder_id is None and req.to_location is None:
        raise HTTPException(status_code=400, detail="Must provide either to_holder_id or to_location.")
    if req.to_holder_id is not None and req.to_location is not None:
        raise HTTPException(status_code=400, detail="Cannot transfer to both Person and Location.")

    # Then resolve, then decide. 404 for what the caller cannot see, 403 only
    # for what they can -- see assign_owner above, which carried the same
    # unscoped raw-id lookup and closes the same cross-unit hole.
    #
    # Both lines sit OUTSIDE the try below, and they stay there even though
    # H1-10.5 closed DATA-H6. The `except HTTPException: raise` clause added
    # then means a gate moved inside would no longer be flattened into a 500 --
    # but it would still run after the block had begun mutating rows, and a
    # decision that can refuse belongs before the work, not inside it. The
    # ordering was load-bearing for one reason and is kept for a better one.
    item = get_scoped_equipment_or_404(db, current_user, req.equipment_id)
    authz.require(db, current_user.id, Capability.TRANSFER, item.group_id)

    # The target is resolved HERE, above the try, and that move is DATA-H6.
    # It used to sit inside, where the broad except re-wrapped its 404 as a
    # 500 with the original detail embedded in the message -- a wrong status
    # code and an internals leak from one misplacement. It is a pure read, so
    # it belongs with the other resolution steps regardless.
    # `is not None`, not truthiness. The XOR above already decided which branch
    # this request is, using `is None` -- so a to_holder_id of 0 passed XOR as a
    # person transfer and then failed this test as a falsy value, fell through
    # to the location branch, and stranded the item with no holder and a null
    # location. 200 OK, and the item belonged to nobody and sat nowhere.
    #
    # Pre-existing, and inherited by the branch below, so the two conditions are
    # written to agree by construction rather than by coincidence.
    target = None
    destination = None
    if req.to_holder_id is not None:
        target = db.query(models.User).filter(models.User.id == req.to_holder_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Target user not found")

        # The DESTINATION gate. Until H1-10.5 the route asked only whether the
        # caller could take the item OUT of where it was, then re-derived its
        # group from the new holder without asking whether they could put it
        # THERE -- so a commander could push an item into a neighbouring unit
        # and out of their own sight. Placing an item into a group is the same
        # authority as removing it from one, so it is the same verb at both
        # ends.
        #
        # What that means in the graph is the argument for one verb rather than
        # two: a cross-company handover now needs authority over a node
        # containing BOTH companies, which is their battalion. The algebra says
        # "the level that contains both parties" without being told to.
        #
        # One guard, not two. None means the target is a member of nothing and
        # the item does not move, so there is no destination to authorise --
        # and require() refuses a None group, so the check is load-bearing
        # rather than tidy.
        #
        # A `destination != item.group_id` clause stood here too and was
        # deleted: for a within-unit handover the two groups are the same, so
        # it asked require() the identical question the line above already
        # answered. Mutation proved it -- the clause could be inverted with the
        # whole suite green, because no caller can reach it holding TRANSFER
        # over the source and not over the source. That is DATA-H9's shape, and
        # the same redundancy H1-8 deleted from _creation_group_id. It cost one
        # avoidable query on the common path; a predicate that can never change
        # an answer is worth more than that.
        destination = authz.primary_group_id(db, target.id)
        if destination is not None:
            authz.require(db, current_user.id, Capability.TRANSFER, destination)

    try:
        if target is not None:
            item.holder_user_id = target.id
            item.custom_location = None
            # Possession moves the item to the new holder's unit, and both ends
            # of that move are now authorised above.
            if destination is not None:
                item.group_id = destination

            log = models.TransactionLog(
                equipment_id=item.id,
                involved_user_id=current_user.id,
                event_type="HANDOVER",
                user_status_at_time=current_user.is_active_duty,
                location=f"User:{target.full_name}" 
            )
            db.add(log)
            result_msg = {"status": "Transferred", "new_holder": target.full_name}

        else:
            # group_id is deliberately untouched: an item is AT a location but
            # still BELONGS TO its unit. This branch clears holder_user_id, so
            # the holder arm of the scoping predicate stops applying and the
            # group becomes the only thing keeping the item visible to anyone --
            # clearing it here would strand the item permanently.
            item.custom_location = req.to_location
            item.holder_user_id = None
            
            log = models.TransactionLog(
                equipment_id=item.id,
                involved_user_id=current_user.id,
                event_type="HANDOVER_LOC",
                user_status_at_time=current_user.is_active_duty,
                location=req.to_location
            )
            db.add(log)
            result_msg = {"status": "Transferred", "location": req.to_location}

        item.actual_location_id = None
        item.last_verified_at = clock.utcnow()
        
        db.commit()
        db.refresh(item)
        return result_msg

    except HTTPException:
        # Defence in depth for DATA-H6. Moving the target lookup out of the try
        # fixes the instance that existed; this stops the next one. Anything
        # raised in here that already carries a deliberate status code keeps it
        # instead of being flattened into a 500 that leaks its detail string.
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Transfer Error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal Server Error during transfer: {str(e)}")

@router.post("/equipment/{equipment_id}/verify")
def verify_equipment_daily(
    equipment_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Resolver in place of the raw-id lookup. The pair it replaced -- 404 when
    # no such row exists, 403 when one does and the caller does not hold it --
    # was itself an enumeration oracle: two different answers told a caller
    # exactly which ids had been issued across the whole force.
    #
    # The holder check stays as it is, and deliberately does NOT become
    # require_status_authority. That helper is possession-OR-grant, which here
    # would be strictly WIDER than today: it would let a commander file a daily
    # presence confirmation for kit they are not carrying, which is the one
    # thing this route exists to record. Narrower than the helper, so the helper
    # is the wrong tool rather than an oversight.
    item = get_scoped_equipment_or_404(db, current_user, equipment_id)

    if item.holder_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission Denied: You can only verify equipment you hold.")

    item.last_verified_at = clock.utcnow()
    
    trans_log = models.TransactionLog(
        equipment_id=item.id,
        involved_user_id=current_user.id,
        event_type="VERIFICATION",
        user_status_at_time=current_user.is_active_duty,
        timestamp=clock.utcnow()
    )
    db.add(trans_log)
    
    db.commit()
    
    new_status = get_daily_status(item.last_verified_at)
    return {"status": "Verified", "compliance": new_status}
