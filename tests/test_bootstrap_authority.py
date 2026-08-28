"""The grants the first MASTER account is created with (H1-8).

`bootstrap_admin` is the only supported way to create the first administrator,
and it is not reachable over HTTP, so nothing in the suite ever ran it. That was
survivable while `equipment.py` carried `or current_user.role == "master"`: an
account with no group, no membership and no grant still worked, because the role
was doing the work. H1-8 deleted that comparison, which made the grants this
module asserts the ONLY reason a bootstrapped master can do anything.

The tests are written against `grant_root_authority` rather than the full
`bootstrap_admin`, which runs migrations, generates a password and prints it.
The authority is the part that changed and the part that can silently produce a
useless account.
"""
import pytest

from backend import authz, models
from backend.bootstrap_admin import (
    ROOT_CAPABILITIES,
    ROOT_GROUP_NAME,
    already_bootstrapped,
    grant_root_authority,
)
from backend.enums import Capability


@pytest.fixture
def admin(db_session):
    """A user with no group, no membership and no grant -- what bootstrap builds
    before grant_root_authority runs. H1-12 drops role entirely; nothing in
    this module ever read it -- the account's authority was always the grants
    asserted below, not a string on its own row."""
    user = models.User(personal_number="u_boot", full_name="First Admin")
    db_session.add(user)
    db_session.flush()
    return user


def commands(db_session, user, capability):
    """The group NAMES in extent(user, capability).

    Deliberately not "does a Grant row exist". A Grant pointing at a group with
    no closure rows authorises nothing -- see the rebuild in
    grant_root_authority, and conftest.create_group, which exists because of the
    same edge. Reading the extent is what tells the two apart; counting rows
    reports success for an account that commands nothing.
    """
    names = {group.id: group.name for group in db_session.query(authz.Group)}
    return {names[group_id] for group_id in db_session.scalars(authz.extent(user.id, capability))}


def test_an_empty_database_gets_a_root_the_admin_commands(db_session, admin):
    """No org chart to attach to, so one is created.

    The extent assertion is the whole test. Creating the group and the grant is
    the easy half; a Group added without rebuild_closure carries no closure row
    at all, not even the depth-0 self-row desc(G) is built from, so it is
    reachable by no grant including the direct one issued over it. Every row
    would look right and the account would command nothing.
    """
    roots = grant_root_authority(db_session, admin)

    assert roots == [ROOT_GROUP_NAME]
    for capability in Capability:
        assert commands(db_session, admin, capability) == {ROOT_GROUP_NAME}, capability


def test_the_admin_stands_in_the_root_as_well_as_commanding_it(db_session, admin):
    """Membership and grant are different facts, and both are needed.

    The grant says what this account commands. The membership says where it
    stands, and H1-6 derives a new item's group from the creator's membership --
    so a master with grants and no membership passes the gate on
    create_equipment and then puts the item in no group at all.
    """
    grant_root_authority(db_session, admin)

    assert authz.primary_group_id(db_session, admin.id) is not None


def test_an_existing_graph_is_used_rather_than_a_second_root(db_session, admin, group_graph):
    """A tree built by hand before bootstrap runs must not get a rival root.

    A second, disconnected ROOT would leave the master commanding an empty
    subtree while the real organisation sat outside every extent they hold --
    the exact failure this function exists to prevent, wearing the shape of
    success.
    """
    roots = grant_root_authority(db_session, admin)

    assert roots == ["188"]
    assert db_session.query(authz.Group).filter_by(name=ROOT_GROUP_NAME).count() == 0
    assert commands(db_session, admin, Capability.VIEW) == {
        "188", "188/53", "188/53/A", "188/53/B",
    }


def test_the_grant_lands_on_the_root_and_not_on_every_group(db_session, admin, group_graph):
    """One grant, at the top, and the closure does the rest.

    Granting over every group would produce the same extent and be wrong for a
    reason no visibility test could see: authority would stop being positional,
    so a group added later would fall outside the master's reach and nothing
    would say so.
    """
    grant_root_authority(db_session, admin)

    granted = {
        grant.group_id for grant in db_session.query(authz.Grant).filter_by(user_id=admin.id)
    }
    assert granted == {group_graph["188"].id}


def test_every_root_is_covered_when_the_graph_has_several(db_session, admin, group_graph):
    """Two disconnected trees are two roots, and a master commands both.

    "The root" is not a thing this script can look up -- it cannot know the
    organisation it is bootstrapping. Parentless is the only definition
    available, and it has to be applied to all of them.
    """
    from tests.conftest import create_group

    other = create_group(db_session, "920/Other")

    roots = grant_root_authority(db_session, admin)

    assert sorted(roots) == ["188", "920/Other"]
    assert commands(db_session, admin, Capability.TRANSFER) == {
        "188", "188/53", "188/53/A", "188/53/B", other.name,
    }


def test_already_bootstrapped_is_false_until_a_root_membership_exists(
    db_session, admin
):
    """The H1-12 replacement for `role == UserRole.MASTER`.

    Before grant_root_authority runs there is no root and no membership, so
    the guard must not refuse -- an empty database is exactly the state the
    first bootstrap has to succeed from.
    """
    assert already_bootstrapped(db_session) is False

    grant_root_authority(db_session, admin)

    assert already_bootstrapped(db_session) is True


def test_already_bootstrapped_is_false_for_a_hand_built_tree_with_no_master(
    db_session, group_graph
):
    """An operator who built their own org chart first is not "bootstrapped".

    Roots exist here (the fixture tree), but nobody has been granted root
    membership by bootstrap_admin yet -- the guard has to tell that apart from
    the case above, or a real deployment could never bootstrap onto a
    hand-built tree.
    """
    assert already_bootstrapped(db_session) is False


def test_the_root_capability_list_is_the_whole_enum_and_says_so():
    """The enum stopped being load-bearing in a file nobody opens.

    grant_root_authority used to iterate `Capability` directly, so every verb
    added to the enum was conferred on bootstrapped masters silently -- H1-9 and
    H1-10 added two each without anyone deciding they belonged to this account.
    That is a defensible default for an account defined as commanding
    everything, and it was still a decision being made by nobody.

    The list is explicit now, and this asserts it equals the enum. The point is
    NOT that the two differ -- they do not, and the seeded master holds all
    seven. The point is that adding a verb now fails here, in both directions,
    and forces the choice to be made out loud.
    """
    assert set(ROOT_CAPABILITIES) == set(Capability)
    assert len(ROOT_CAPABILITIES) == len(set(ROOT_CAPABILITIES)), "a duplicate row"
