"""Signing in, accounts and keys, and an organization's people, groups, policies and invitations
(api-server.md §2, §3, §4)."""

from __future__ import annotations

import logging
from typing import Any

from pymongo.errors import DuplicateKeyError

from ..agents.store import Agents
from ..doors.calls import ANYONE, Caller, Controller, Refused, endpoint
from ..doors.limits import Limits
from ..settings.organization import OrgSettings
from ..trail.trail import Trail
from ..utils import Settings
from .leaving import Leaving
from .mail import Mailer
from .permissions import Bounds, Rules
from .store import ApiKeys, Deployment, Groups, Links, Organizations, Passwords, Policies, Sessions, Users

log = logging.getLogger("api_server")


class People:
    """What several doors do the same way: founding an organization, signing a person in, saying who they are."""

    @staticmethod
    def found(org_name: str, name: str, email: str, password: str) -> dict[str, Any]:
        """An organization with its Everyone and Administrators, and its first administrator."""
        org = Organizations().create(org_name)
        groups = Groups()
        groups.create(org["_id"], Groups.EVERYONE, ["member"], everyone=True)
        admins = groups.create(org["_id"], Groups.ADMINISTRATORS, ["administrator"])
        return Users().create(org["_id"], email, name, password, [admins["_id"]])

    @staticmethod
    def signed_in(person: dict[str, Any], remember: Any) -> dict[str, Any]:
        """A session, and its line on the trail: a call before signing in is nobody's until it succeeds."""
        Users().set(person["_id"], last_login=Users.now())
        Trail().record(Caller("person", person=person), "auth:session:create")
        return {"token": Sessions().create(person, remember is True), "person": Users.public(person)}

    @staticmethod
    def operator(person: dict[str, Any]) -> bool:
        org = Settings.read().operator_org or Deployment().get().get("operator_org")
        return person["org_id"] == org and Rules().allows(person, "nodes:authority:shared") == "allow"

    @staticmethod
    def password(given: Any, email: Any, address: str) -> dict[str, Any]:
        """The person whose password this is. Tries count for the account and address together, and for the
        address: nobody else's tries shut a person out."""
        email = str(email or "").strip().lower()
        Limits().check(f"signin:{address}", 20, 900, "sign-in tries from this address")
        Limits().check(f"signin:{email}:{address}", 5, 900, "sign-in tries")
        person = Users().by_email(email)
        if not Passwords.matches(str(given or ""), (person or {}).get("password_hash")) or person["status"] != "active" \
                or not Organizations().active(person["org_id"]):
            raise Refused("refused", "That email and password don't match an account.")
        Limits().clear(f"signin:{email}:{address}")
        return person


class AuthEndpoints(Controller):
    NAME = "Auth:Session"

    @endpoint("Sign in", by=ANYONE)
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person = People.password(data.get("password"), data.get("email"), caller.address)
        if person.get("must_change_password"):
            raise Refused("conflict", "Choose a password of your own first.", change_required=True)
        return People.signed_in(person, data.get("remember"))

    @endpoint("Replace a password handed over, and sign in", by=ANYONE)
    def first(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person = People.password(data.get("current_password"), data.get("email"), caller.address)
        new = Passwords.check(data.get("new_password"))
        person = Users().set(person["_id"], password_hash=Passwords.hash(new), must_change_password=False)
        return People.signed_in(person, data.get("remember"))

    @endpoint("Sign out")
    def end(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Sessions().end(caller.session)
        return {}


class MeEndpoints(Controller):
    NAME = "Auth:Me"

    @endpoint("See who you are", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        org = Organizations().col.find_one({"_id": caller.org_id}, {"name": 1}) or {}
        return {"person": Users.public(caller.person), "organization": {"org_id": caller.org_id, "name": org.get("name")},
                "allowed": Rules().allowed(caller.person), "operator": People.operator(caller.person),
                "speech": OrgSettings().of(caller.org_id)["speech"] != "none", "catalog": Rules.described()}


class PasswordEndpoints(Controller):
    NAME = "Auth:Password"

    @endpoint("Change your password")
    def change(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if not Passwords.matches(str(data.get("current_password") or ""), caller.person["password_hash"]):
            raise Refused("refused", "Your current password is not that.")
        Users().set(caller.user_id, password_hash=Passwords.hash(Passwords.check(data.get("new_password"))))
        Sessions().col.delete_many({"user_id": caller.user_id, "_id": {"$ne": caller.session}})
        return {}

    @endpoint("Ask for a link to reset a forgotten password", by=ANYONE)
    def forgot(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """The same answer whether the address has an account or not."""
        email = str(data.get("email") or "").strip().lower()
        Limits().check(f"mail:{caller.address}", 10, 86400, "links from this address")
        person = Users().by_email(email)
        if person and Mailer().ready and not Links().recent("reset", email) \
                and not Limits().take(f"mail:{email}", 5, 86400):
            token = Links().create("reset", email, user_id=person["_id"])
            Mailer().send(email, "Reset your DecentAI password", ["Someone asked to reset your password. If it was "
                          "you, follow this link within an hour."], f"{Settings.read().app_url}/reset?token={token}")
        return {}

    @endpoint("Check a reset link", by=ANYONE, changes=False)
    def check(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        link = Links().peek("reset", data.get("token"))
        return {"valid": link is not None, "email": (link or {}).get("email", "")}

    @endpoint("Reset a forgotten password through its link", by=ANYONE)
    def reset(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        new = Passwords.check(data.get("password"))
        link = Links().spend("reset", data.get("token"))
        if link is None:
            raise Refused("not_found", "That link was used, or has ended.")
        Users().set(link["user_id"], password_hash=Passwords.hash(new), must_change_password=False)
        Sessions().end_all(link["user_id"])
        return {}


class JoinEndpoints(Controller):
    NAME = "Auth:Join"

    @endpoint("See whether first run is done, and what joining asks", by=ANYONE, changes=False)
    def status(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        settings = Settings.read()
        return {"set_up": Deployment().get().get("first_run_done") is True, "mail": settings.mail,
                "signup": settings.signup == "open" and settings.mail}

    @endpoint("Make the first organization and its administrator, with the setup code", by=ANYONE)
    def setup(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Limits().check(f"setup:{caller.address}", 10, 900, "setup tries")
        if not Deployment().claim_first_run(str(data.get("code") or "")):
            raise Refused("refused", "That is not the setup code, or first run is done.")
        try:
            person = People.found(data.get("organization"), data.get("name"), data.get("email"),
                                  Passwords.check(data.get("password")))
        except (ValueError, DuplicateKeyError):
            Deployment().set(first_run_done=False)
            raise
        Deployment().set(operator_org=person["org_id"], setup_code="")
        return People.signed_in(person, False)

    @endpoint("Ask for an organization of your own, by a link mailed to you", by=ANYONE)
    def signup(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        settings = Settings.read()
        if settings.signup != "open" or not settings.mail:
            raise Refused("refused", "This deployment takes no sign-ups.")
        email = Users.clean_email(data.get("email"))
        password = Passwords.check(data.get("password"))
        Organizations.clean_name(data.get("organization"))
        Limits().check(f"mail:{caller.address}", 10, 86400, "links from this address")
        if not Links().recent("signup", email) and not Limits().take(f"mail:{email}", 5, 86400) \
                and Users().by_email(email) is None:
            token = Links().create("signup", email, organization=data.get("organization"),
                                   name=Users.clean_name(data.get("name")), password_hash=Passwords.hash(password))
            Mailer().send(email, "Confirm your DecentAI organization", ["Follow this link within an hour to make "
                          "your organization."], f"{settings.app_url}/signup?token={token}")
        return {}

    @endpoint("Make the organization a sign-up link asked for", by=ANYONE)
    def confirm(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        link = Links().spend("signup", data.get("token"))
        if link is None:
            raise Refused("not_found", "That link was used, or has ended.")
        person = People.found(link["organization"], link["name"], link["email"], Passwords.temporary())
        person = Users().set(person["_id"], password_hash=link["password_hash"])
        return People.signed_in(person, False)

    @endpoint("See an invitation", by=ANYONE, changes=False)
    def invitation(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        link = Links().peek("invitation", data.get("token"))
        if link is None:
            raise Refused("not_found", "That invitation was used, withdrawn, or has ended.")
        org = Organizations().col.find_one({"_id": link["org_id"]}, {"name": 1}) or {}
        return {"email": link["email"], "organization": org.get("name", "")}

    @endpoint("Accept an invitation", by=ANYONE)
    def accept(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        password, name = Passwords.check(data.get("password")), Users.clean_name(data.get("name"))
        link = Links().spend("invitation", data.get("token"))
        if link is None:
            raise Refused("not_found", "That invitation was used, withdrawn, or has ended.")
        try:
            person = Users().create(link["org_id"], link["email"], name, password, link.get("groups") or [])
        except DuplicateKeyError:
            raise Refused("conflict", "That address has an account already.") from None
        return People.signed_in(person, False)


class ProfileEndpoints(Controller):
    NAME = "Account:Profile"

    @endpoint("See your profile", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"person": Users.public(caller.person), "preferences": caller.person.get("preferences") or {}}

    @endpoint("Change your name and preferences")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if "name" in data:
            changes["name"] = Users.clean_name(data["name"])
        if isinstance(data.get("preferences"), dict):
            if len(str(data["preferences"])) > 8000:
                raise ValueError("Preferences are at most 8,000 characters.")
            changes["preferences"] = data["preferences"]
        return {"person": Users.public(Users().set(caller.user_id, **changes) or caller.person)}

    @endpoint("See the people and groups of your organization, to share with", changes=False)
    def peers(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"people": [{"user_id": p["_id"], "name": p["name"], "email": p["email"]}
                           for p in Users().in_org(caller.org_id) if p["status"] == "active"],
                "groups": [{"group_id": g["_id"], "name": g["name"], "everyone": g["everyone"],
                            "administrators": g["name"] == Groups.ADMINISTRATORS} for g in Groups().in_org(caller.org_id)]}


class KeyEndpoints(Controller):
    NAME = "Settings:ApiKey"

    @endpoint("See your API keys", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"keys": [ApiKeys.public(k) for k in ApiKeys().col.find({"user_id": caller.user_id})]}

    @endpoint("Make an API key; it is shown once")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        key, doc = ApiKeys().create(caller.person, data.get("name"), data.get("days", 90))
        return {"key": key, **ApiKeys.public(doc)}

    @endpoint("Revoke an API key")
    def revoke(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if not ApiKeys().col.delete_one({"_id": str(data.get("key_id") or ""), "user_id": caller.user_id}).deleted_count:
            raise Refused("not_found", "No such key.")
        return {}


class OrganizationEndpoints(Controller):
    NAME = "IAM:Organization"

    @endpoint("See the organization", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        org = Organizations().col.find_one({"_id": caller.org_id}, {"name": 1, "created_at": 1}) or {}
        return {"organization": {"org_id": caller.org_id, "name": org.get("name"), "created_at": org.get("created_at")}}

    @endpoint("Rename the organization")
    def rename(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        Organizations().col.update_one({"_id": caller.org_id}, {"$set": {"name": Organizations.clean_name(data.get("name"))}})
        return self.get(data, caller)


class UserEndpoints(Controller):
    NAME = "IAM:User"

    def _target(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person = Users().get_in(caller.org_id, data.get("user_id"))
        if person is None:
            raise Refused("not_found", "No such person.")
        Bounds().over(caller.person, person)
        return person

    def _groups(self, caller: Caller, ids: Any) -> list[str]:
        ids = [str(i) for i in ids or []]
        found = Groups().in_org(caller.org_id, ids)
        if len(found) != len(set(ids)) or any(g["everyone"] for g in found):
            raise ValueError("Name groups of this organization, Everyone aside.")
        Bounds().holds(caller.person, Rules().statements({"org_id": caller.org_id, "groups": ids}))
        return ids

    @endpoint("See the organization's people", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"users": [Users.public(p) for p in Users().in_org(caller.org_id)]}

    @endpoint("Add a person with a temporary password, where no mail is set up")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if Mailer().ready:
            raise Refused("conflict", "People join this deployment by invitation.")
        password = Passwords.temporary()
        try:
            person = Users().create(caller.org_id, data.get("email"), data.get("name"), password,
                                    self._groups(caller, data.get("groups")), temporary=True)
        except DuplicateKeyError:
            raise Refused("conflict", "That address can't be added.") from None
        return {"user": Users.public(person), "password": password}

    @endpoint("Reset a person's password: a mailed link, or a temporary password where there is no mail")
    def reset_password(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person = self._target(data, caller)
        if Mailer().ready:
            token = Links().create("reset", person["email"], user_id=person["_id"])
            Mailer().send(person["email"], "Reset your DecentAI password", ["An administrator asked for your "
                          "password to be reset. Follow this link within an hour."],
                          f"{Settings.read().app_url}/reset?token={token}")
            return {"mailed": True}
        password = Passwords.temporary()
        Users().set(person["_id"], password_hash=Passwords.hash(password), must_change_password=True)
        Sessions().end_all(person["_id"])
        return {"password": password}

    @endpoint("Place a person in groups")
    def set_groups(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person, groups = self._target(data, caller), self._groups(caller, data.get("groups"))
        Bounds().keeps_access(caller.org_id, lambda: Users().set(person["_id"], groups=groups),
                              lambda: Users().set(person["_id"], groups=person["groups"]))
        return {"user": Users.public(Users().col.find_one({"_id": person["_id"]}))}

    @endpoint("Disable or enable a person")
    def set_status(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person, status = self._target(data, caller), str(data.get("status") or "")
        if status not in ("active", "disabled") or person["_id"] == caller.user_id:
            raise ValueError("The status is active or disabled, and not your own.")
        Bounds().keeps_access(caller.org_id, lambda: Users().set(person["_id"], status=status),
                              lambda: Users().set(person["_id"], status=person["status"]))
        if status == "disabled":
            Sessions().end_all(person["_id"])
        return {"user": Users.public(Users().col.find_one({"_id": person["_id"]}))}

    @endpoint("See what removing a person will touch", changes=False)
    def leaving(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"counts": Leaving().count(caller.org_id, self._target(data, caller)["_id"])}

    @endpoint("Remove a person, handing what they shared, their data and their files to a successor")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        person = self._target(data, caller)
        successor = Users().get_in(caller.org_id, data.get("successor_id") or caller.user_id)
        if person["_id"] == caller.user_id or successor is None or successor["_id"] == person["_id"]:
            raise ValueError("Name a successor in this organization, and not the person removed or yourself removed.")
        Bounds().keeps_access(caller.org_id, lambda: Users().set(person["_id"], status="disabled"),
                              lambda: Users().set(person["_id"], status=person["status"]))
        Leaving().hand_on(caller.org_id, person["_id"], successor["_id"])
        Sessions().end_all(person["_id"])
        ApiKeys().col.delete_many({"user_id": person["_id"]})
        Users().col.delete_one({"_id": person["_id"]})
        return {}


class GroupEndpoints(Controller):
    NAME = "IAM:Group"

    def _group(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        group = Groups().get_in(caller.org_id, data.get("group_id"))
        if group is None:
            raise Refused("not_found", "No such group.")
        return group

    @endpoint("See the organization's groups", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        return {"groups": [{**self._public(g), "members": g["members"]} for g in Groups().counted(caller.org_id)]}

    @endpoint("See one group and its people", changes=False)
    def get(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        group = self._group(data, caller)
        people = Users().in_org(caller.org_id) if group["everyone"] else \
            list(Users().col.find({"org_id": caller.org_id, "groups": group["_id"]}))
        return {"group": self._public(group), "users": [Users.public(p) for p in people]}

    @endpoint("Make a group")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        try:
            return {"group": self._public(Groups().create(caller.org_id, data.get("name"), []))}
        except DuplicateKeyError:
            raise Refused("conflict", "A group has that name already.") from None

    @endpoint("Rename a group")
    def rename(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        group = self._group(data, caller)
        if group["everyone"] or group["name"] == Groups.ADMINISTRATORS:
            raise Refused("conflict", "Everyone and Administrators keep their names.")
        Groups().col.update_one({"_id": group["_id"]}, {"$set": {"name": Groups.clean_name(data.get("name"))}})
        Organizations().access_changed(caller.org_id)
        return {"group": self._public({**group, "name": Groups.clean_name(data.get("name"))})}

    @endpoint("Give a group its policies")
    def set_policies(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        group, names = self._group(data, caller), [str(p) for p in data.get("policies") or []]
        custom = [n for n in names if n not in Rules.BUILT_IN]
        found = Policies().in_org(caller.org_id, custom)
        if len(found) != len(set(custom)):
            raise ValueError("Name policies of this organization, or Administrator or Member.")
        added = [s for n in names if n not in group["policies"]
                 for s in (Rules.BUILT_IN[n]["statements"] if n in Rules.BUILT_IN else
                           next(p["statements"] for p in found if p["_id"] == n))]
        Bounds().holds(caller.person, added)
        for member in Users().col.find({"org_id": caller.org_id, **({} if group["everyone"] else {"groups": group["_id"]})}):
            Bounds().over(caller.person, member)
        Bounds().keeps_access(caller.org_id,
                              lambda: Groups().col.update_one({"_id": group["_id"]}, {"$set": {"policies": names}}),
                              lambda: Groups().col.update_one({"_id": group["_id"]}, {"$set": {"policies": group["policies"]}}))
        return {"group": self._public({**group, "policies": names})}

    @endpoint("Delete a group")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        group = self._group(data, caller)
        if group["everyone"] or group["name"] == Groups.ADMINISTRATORS:
            raise Refused("conflict", "Everyone and Administrators stay.")
        members = list(Users().col.find({"groups": group["_id"]}))
        for member in members:
            Bounds().over(caller.person, member)
        Users().col.update_many({"groups": group["_id"]}, {"$pull": {"groups": group["_id"]}})
        Agents().col.update_many({"org_id": caller.org_id}, {"$pull": {"access": {"group_id": group["_id"]}}})
        Groups().col.delete_one({"_id": group["_id"]})
        Organizations().access_changed(caller.org_id)
        return {}

    @staticmethod
    def _public(group: dict[str, Any]) -> dict[str, Any]:
        return {"group_id": group["_id"], "name": group["name"], "policies": group["policies"],
                "everyone": group["everyone"], "administrators": group["name"] == Groups.ADMINISTRATORS}


class PolicyEndpoints(Controller):
    NAME = "IAM:Policy"

    def _policy(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        policy = Policies().get_in(caller.org_id, data.get("policy_id"))
        if policy is None:
            raise Refused("not_found", "No such policy; the built-in ones can't be changed.")
        return policy

    @endpoint("See the policies, the built-in ones too", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        built = [{"policy_id": key, "name": p["name"], "statements": p["statements"], "built_in": True}
                 for key, p in Rules.BUILT_IN.items()]
        return {"policies": built + [self._public(p) for p in Policies().in_org(caller.org_id)],
                "catalog": Rules.described()}

    @endpoint("Write a policy")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        statements = Rules.check(data.get("statements"))
        Bounds().holds(caller.person, statements)
        doc = {"_id": Policies().new_id(), "org_id": caller.org_id, "name": Groups.clean_name(data.get("name")),
               "statements": statements, "created_at": Policies.now()}
        try:
            Policies().col.insert_one(doc)
        except DuplicateKeyError:
            raise Refused("conflict", "A policy has that name already.") from None
        return {"policy": self._public(doc)}

    @endpoint("Change a policy")
    def update(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        policy, statements = self._policy(data, caller), Rules.check(data.get("statements"))
        Bounds().holds(caller.person, statements)
        Bounds().holds(caller.person, policy["statements"])
        Bounds().keeps_access(caller.org_id,
                              lambda: Policies().col.update_one({"_id": policy["_id"]}, {"$set": {"statements": statements}}),
                              lambda: Policies().col.update_one({"_id": policy["_id"]},
                                                                {"$set": {"statements": policy["statements"]}}))
        return {"policy": self._public({**policy, "statements": statements})}

    @endpoint("Rename a policy")
    def rename(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        policy, name = self._policy(data, caller), Groups.clean_name(data.get("name"))
        Bounds().holds(caller.person, policy["statements"])
        try:
            Policies().col.update_one({"_id": policy["_id"]}, {"$set": {"name": name}})
        except DuplicateKeyError:
            raise Refused("conflict", "A policy has that name already.") from None
        return {"policy": self._public({**policy, "name": name})}

    @endpoint("Delete a policy")
    def delete(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        policy = self._policy(data, caller)
        Bounds().holds(caller.person, policy["statements"])
        holders = list(Groups().col.find({"org_id": caller.org_id, "policies": policy["_id"]}))
        Bounds().keeps_access(
            caller.org_id,
            lambda: Groups().col.update_many({"policies": policy["_id"]}, {"$pull": {"policies": policy["_id"]}}),
            lambda: [Groups().col.update_one({"_id": g["_id"]}, {"$set": {"policies": g["policies"]}}) for g in holders])
        Policies().col.delete_one({"_id": policy["_id"]})
        return {}

    @staticmethod
    def _public(policy: dict[str, Any]) -> dict[str, Any]:
        return {"policy_id": policy["_id"], "name": policy["name"], "statements": policy["statements"],
                "built_in": False}


class InvitationEndpoints(Controller):
    NAME = "IAM:Invitation"
    PER_DAY = 100

    @endpoint("See the invitations not yet accepted", changes=False)
    def list(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        found = Links().col.find({"kind": "invitation", "org_id": caller.org_id, "expires_at": {"$gt": Links.now()}})
        return {"invitations": [{"invitation_id": link["_id"][:16], "email": link["email"],
                                 "groups": link.get("groups", []), "expires_at": link["expires_at"]} for link in found]}

    @endpoint("Invite a person by mail")
    def create(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        if not Mailer().ready:
            raise Refused("conflict", "This deployment sends no mail: add people with a temporary password.")
        email = Users.clean_email(data.get("email"))
        groups = UserEndpoints()._groups(caller, data.get("groups"))
        if Links().recent("invitation", email):
            raise Refused("too_many", "An invitation went to that address a moment ago.", retry_after=60)
        Limits().check(f"invite:{caller.org_id}", self.PER_DAY, 86400, "invitations today")
        if Users().by_email(email) is None:
            Links().col.delete_many({"kind": "invitation", "org_id": caller.org_id, "email": email})
            token = Links().create("invitation", email, org_id=caller.org_id, groups=groups)
            Mailer().send(email, "You're invited to DecentAI", ["You're invited to join an organization on DecentAI. "
                          "Follow this link within 7 days."], f"{Settings.read().app_url}/invitation?token={token}")
        return {"email": email}

    @endpoint("Withdraw an invitation")
    def revoke(self, data: dict[str, Any], caller: Caller) -> dict[str, Any]:
        prefix = str(data.get("invitation_id") or "")
        if len(prefix) != 16 or not set(prefix) <= set("0123456789abcdef") or not Links().col.delete_one(
                {"kind": "invitation", "org_id": caller.org_id, "_id": {"$regex": f"^{prefix}"}}).deleted_count:
            raise Refused("not_found", "No such invitation.")
        return {}
