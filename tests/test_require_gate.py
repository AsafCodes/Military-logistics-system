"""The gate: may(P, C, R), and the 403 it raises.

test_query_surface.py asks the algebra a question and reads a set back. This
module asks the one question the application actually asks -- may this
principal do this thing to this resource -- and checks the answer, the refusal,
and the order the refusal has to come in.

Two properties here are structural rather than incidental, and both pass by
accident if tested loosely:

  * authority points DOWN. A grant on a battalion reaches its companies; a
    grant on a company reaches nothing above it. Asserted in both directions,
    because a closure join with its arguments transposed satisfies the first
    direction perfectly.

  * 404 comes before 403. The resolver answers whether the caller can SEE the
    resource; only then does the gate answer whether they may act on it. Run
    the other way round, a 403 confirms an id the caller was never allowed to
    know existed.

Every test starts from an actor holding nothing, so the grant under test is
the only authority in play and no assertion can be satisfied by a fixture's
leftovers.
"""
import pytest
from fastapi import HTTPException

from backend import authz, models
from backend.dependencies import get_scoped_equipment_or_404
from backend.enums import Capability
from tests.test_query_surface import recorded


@pytest.fixture
def actor(db_session):
    """A user with no grants, no memberships and no profile."""
    user = models.User(personal_number="u_gate_actor", full_name="Gate Actor")
    db_session.add(user)
    db_session.flush()
    return user


def grant(db, actor, groups, where, capability=Capability.TRANSFER):
    db.add(authz.Grant(
        user_id=actor.id, group_id=groups[where].id, capability=capability.value
    ))
    db.flush()


def allowed(db, actor, groups, where, capability=Capability.TRANSFER):
    return authz.may(db, actor.id, capability, groups[where].id)


# --- which way authority travels -------------------------------------------

def test_a_grant_on_the_resources_own_group_permits(db_session, group_graph, actor):
    grant(db_session, actor, group_graph, "188/53/A")
    assert allowed(db_session, actor, group_graph, "188/53/A")


def test_a_grant_on_an_ancestor_permits(db_session, group_graph, actor):
    """Positional authority, which is the entire reason for the closure join.

    The same grant on the root would mean the whole force. Here it sits two
    levels up and still reaches the company.
    """
    grant(db_session, actor, group_graph, "188")
    assert allowed(db_session, actor, group_graph, "188/53/A")
    assert allowed(db_session, actor, group_graph, "188/53")


def test_a_grant_on_a_descendant_permits_nothing_above_it(db_session, group_graph, actor):
    """The mirror of the test above, and the one that catches a transposed join.

    ancestor_id and descendant_id swapped in extent() would leave the ancestor
    case passing and fail only here.
    """
    grant(db_session, actor, group_graph, "188/53/A")
    assert not allowed(db_session, actor, group_graph, "188/53")
    assert not allowed(db_session, actor, group_graph, "188")


def test_a_grant_on_a_sibling_permits_nothing(db_session, group_graph, actor):
    grant(db_session, actor, group_graph, "188/53/B")
    assert not allowed(db_session, actor, group_graph, "188/53/A")


def test_no_grant_permits_nothing(db_session, group_graph, actor):
    assert not allowed(db_session, actor, group_graph, "188/53/A")
    assert not allowed(db_session, actor, group_graph, "188")


def test_a_group_id_nothing_knows_about_is_refused_quietly(db_session, group_graph, actor):
    """An unknown id answers False rather than raising.

    A gate that blows up on a bad id reports 500 where it means 403, and the
    difference is visible to whoever is probing.
    """
    grant(db_session, actor, group_graph, "188")
    assert authz.may(db_session, actor.id, Capability.TRANSFER, 999999) is False


# --- verbs do not leak into one another ------------------------------------

@pytest.mark.parametrize("held", list(Capability))
def test_each_verb_answers_only_for_itself(db_session, group_graph, actor, held):
    """Every member of the enum, held in turn, asked about every other member.

    Seeing an item and being allowed to move it are different facts. This is
    what fails if the capability filter is ever dropped from the join -- which
    would otherwise look like a harmless simplification, since almost every
    grant in the suite happens to be VIEW.

    Both directions matter and the parametrisation gets them for free: a VIEW
    grant must not answer for TRANSFER, and a TRANSFER grant must not answer
    for VIEW. Written as a pair of hand-rolled tests, the pair could pass by
    one verb simply being inert.

    Driven off Capability itself rather than a written-out list, so a verb
    added later is covered the moment it is declared -- which is exactly when
    "add only verbs with an enforcing route" is easiest to forget.
    """
    grant(db_session, actor, group_graph, "188/53/A", held)

    for capability in Capability:
        granted = allowed(db_session, actor, group_graph, "188/53/A", capability)
        assert granted == (capability is held), (
            f"a {held.value} grant answered {granted} for {capability.value}"
        )


def test_a_bare_string_capability_is_refused_loudly(db_session, group_graph, actor):
    """Inherited from extent()'s .value read, and worth restating at the gate.

    A gate that answers False for a mistyped verb denies everything and looks
    like it is working.
    """
    with pytest.raises(AttributeError):
        authz.may(db_session, actor.id, "VIEW", group_graph["188"].id)


# --- a resource in no group ------------------------------------------------

def test_a_resource_in_no_group_is_refused_even_from_the_root(db_session, group_graph, actor):
    """Still reachable after H1-11, from the caller rather than the resource.

    This said "Equipment.group_id stays nullable until H1-11, so this is
    reachable", and H1-11 made that column NOT NULL. The case did not go with
    it: primary_group_id() answers None for a user who is a member of nothing,
    and equipment.py's _creation_group_id hands that to require() directly --
    which is how creation fails closed for a user with nowhere to put an item.

    The actor holds the root and can therefore reach every group there is --
    and still may not act on something that is in none of them. In SQL
    NULL IN (...) is NULL, so without the explicit guard this answer would be
    an accident of how a missing row is read rather than a decision.
    """
    grant(db_session, actor, group_graph, "188")
    assert authz.may(db_session, actor.id, Capability.TRANSFER, None) is False

    with pytest.raises(HTTPException) as excinfo:
        authz.require(db_session, actor.id, Capability.TRANSFER, None)
    assert excinfo.value.status_code == 403


def test_a_resource_in_no_group_is_refused_without_asking_the_database(
    db_session, group_graph, actor
):
    """The guard is a branch, not a query that happens to return nothing.

    Pinned because the difference is invisible in the answer and total in the
    reasoning: a query would mean the denial rests on NULL comparison semantics
    holding identically on both dialects.
    """
    with recorded(db_session.get_bind()) as statements:
        assert not authz.may(db_session, actor.id, Capability.TRANSFER, None)
    assert statements == []


def test_one_question_costs_one_query(db_session, group_graph, actor):
    """The gate runs on every write H1-8 and H1-9 touch.

    extent() returns a selectable so it can be composed instead of materialised;
    a may() that pulled the extent into Python and tested membership there would
    answer identically and pay a round trip per call. That is the shape this
    entry deleted from _creation_group_id, and it should not come back.
    """
    grant(db_session, actor, group_graph, "188")
    # Read outside the block. group_graph commits, which expires its instances,
    # so touching .id inside would refresh the row and be counted as the gate's.
    target_id = group_graph["188/53/A"].id

    with recorded(db_session.get_bind()) as statements:
        authz.may(db_session, actor.id, Capability.TRANSFER, target_id)
    assert len(statements) == 1, statements


# --- membership is not a grant ---------------------------------------------

def test_standing_in_a_group_conveys_no_authority_over_it(db_session, group_graph, actor):
    """The distinction the whole model rests on.

    A private is a member of their company and commands none of it. If this
    ever passes, GroupMembership has quietly become a Grant.
    """
    db_session.add(authz.GroupMembership(
        user_id=actor.id, group_id=group_graph["188/53/A"].id
    ))
    db_session.flush()

    for capability in Capability:
        assert not allowed(db_session, actor, group_graph, "188/53/A", capability)


# test_an_ungranted_master_is_denied used to live here: it hand-set actor.role
# to MASTER and asserted may() still denied them, proving may() has no role
# arm. H1-12 drops the `role` column entirely, so a role value to hand-set no
# longer exists -- the guarantee is now structural rather than something a
# test can demonstrate at runtime. What remains ("no grant permits nothing")
# is test_no_grant_permits_nothing above, unchanged by this account ever
# having been called MASTER.


def test_a_grant_belongs_to_one_user(db_session, group_graph, actor):
    """Authority does not spill between accounts.

    A user_id dropped from the grant filter would make one commander's grant
    everybody's, and every other test here would still pass.
    """
    other = models.User(personal_number="u_gate_other", full_name="Other")
    db_session.add(other)
    db_session.flush()
    grant(db_session, other, group_graph, "188")

    assert authz.may(db_session, other.id, Capability.TRANSFER, group_graph["188/53/A"].id)
    assert not allowed(db_session, actor, group_graph, "188/53/A")


# --- require(), as opposed to may() ----------------------------------------

def test_require_returns_nothing_when_permitted(db_session, group_graph, actor):
    grant(db_session, actor, group_graph, "188")
    assert authz.require(
        db_session, actor.id, Capability.TRANSFER, group_graph["188/53/A"].id
    ) is None


def test_require_raises_403_when_denied(db_session, group_graph, actor):
    with pytest.raises(HTTPException) as excinfo:
        authz.require(
            db_session, actor.id, Capability.TRANSFER, group_graph["188/53/A"].id
        )
    assert excinfo.value.status_code == 403


def test_the_denial_names_the_verb_and_nothing_about_the_resource(
    db_session, group_graph, actor
):
    """The message reaches someone who already knows what they attempted.

    It must not tell them anything else. A group name or id in here would turn
    every refusal into a reconnaissance answer -- and the id is the worse of
    the two, being the handle an enumerator actually wants.

    Digits are excluded wholesale rather than this particular id being excluded
    by name. Asserting `str(target.id) not in detail` passes for a message that
    leaks a DIFFERENT group's id, and collides by accident whenever the id is a
    single digit that appears somewhere in the prose. The message legitimately
    contains no numbers at all, so the wider rule is both stronger and stabler.
    """
    target = group_graph["188/53/A"]
    with pytest.raises(HTTPException) as excinfo:
        authz.require(db_session, actor.id, Capability.TRANSFER, target.id)

    detail = excinfo.value.detail
    assert Capability.TRANSFER.value in detail
    assert target.name not in detail
    assert not any(char.isdigit() for char in detail), detail


# --- the order the two answers have to come in -----------------------------

def test_the_resolver_answers_before_the_gate_does(db_session, mock_matrix_db, group_graph):
    """404 for what you cannot see; 403 only for what you can.

    Both halves against one account, because the pair is the claim -- either
    alone is satisfied by a gate that always returns the same code. Company A's
    TECH is the account that produces both: they hold TA300, so the holder arm
    of the scoping predicate resolves it for them, and their profile carries no
    can_change_assignment_others, so H1-8 issues them no TRANSFER grant. SB200
    is in a company they have no claim on at all.

    This used to be written against Co A's COMMANDER, who held no TRANSFER
    grant either until H1-8 gave them one. The account changed; the property
    did not.
    """
    tech_a = mock_matrix_db["company_tech_a"]
    own = db_session.query(models.Equipment).filter_by(serial_number="TA300").one()
    elsewhere = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()

    visible = get_scoped_equipment_or_404(db_session, tech_a, own.id)
    with pytest.raises(HTTPException) as denied:
        authz.require(db_session, tech_a.id, Capability.TRANSFER, visible.group_id)
    assert denied.value.status_code == 403, "visible, but no authority to act on it"

    with pytest.raises(HTTPException) as hidden:
        get_scoped_equipment_or_404(db_session, tech_a, elsewhere.id)
    assert hidden.value.status_code == 404, "the gate must not be reached at all"


def test_an_invisible_item_and_a_nonexistent_one_are_indistinguishable(
    db_session, mock_matrix_db, group_graph
):
    """What the ordering buys, stated as the property it protects."""
    cmdr_a = mock_matrix_db["company_cmdr_a"]
    elsewhere = db_session.query(models.Equipment).filter_by(serial_number="SB200").one()

    answers = set()
    for target in (elsewhere.id, 999999):
        with pytest.raises(HTTPException) as excinfo:
            get_scoped_equipment_or_404(db_session, cmdr_a, target)
        answers.add((excinfo.value.status_code, excinfo.value.detail))

    assert len(answers) == 1, f"the two cases are distinguishable: {answers}"
