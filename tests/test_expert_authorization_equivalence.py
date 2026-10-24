"""Characterize expert policy reuse without bypassing the surrounding guards."""
from itertools import product

import pytest
from fastapi import HTTPException

from app.api import media, monitoring
from app.domain import expert


def _original_policy(decoded):
    return expert.is_expert_identity(
        decoded if decoded.get("email_verified") is True
        else {**decoded, "email": None}
    )


def test_dictionary_policy_equivalence(monkeypatch):
    monkeypatch.setattr(expert, "EXPERT_ACCOUNT_EMAIL", "primary@example.test")
    monkeypatch.setattr(expert, "EXPERT_ACCOUNT_EMAILS", {"listed@example.test"})
    monkeypatch.setattr(expert, "EXPERT_ACCOUNT_UID", "primary-expert")
    monkeypatch.setattr(expert, "EXPERT_ACCOUNT_UIDS", {"listed-expert"})
    missing = object()
    roles = (missing, None, "", "farmer", "expert", " EXPERT ")
    emails = (missing, None, "", "other@example.test", "primary@example.test", " LISTED@EXAMPLE.TEST ")
    uids = (missing, None, "", "ordinary", "primary-expert", " listed-expert ")
    verified = (missing, None, False, True, 0, 1, "true")
    for values in product(roles, roles, emails, uids, verified):
        decoded = {key: value for key, value in zip(
            ("role", "custom_role", "email", "uid", "email_verified"), values
        ) if value is not missing}
        original = dict(decoded)
        expected = _original_policy(decoded)
        assert expert.is_authorized_expert_identity(decoded) is expected, decoded
        assert monitoring._trusted_expert(decoded) is expected, decoded
        assert media._is_expert(decoded) is expected, decoded
        assert decoded == original


@pytest.mark.parametrize("policy", [monitoring._trusted_expert, media._is_expert])
def test_none_retains_original_attribute_error(policy):
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'get'"):
        policy(None)


@pytest.mark.parametrize("guard", [monitoring._expert, monitoring._farmer,
                                  media._expert, media._farmer])
@pytest.mark.parametrize("decoded", [
    {}, {"uid": "invalid/uid", "role": "expert"},
    {"uid": "expert", "role": "expert", "token_verified_offline": True},
])
def test_uid_and_offline_guards_remain_mandatory(guard, decoded):
    with pytest.raises(HTTPException) as rejected:
        guard(decoded)
    assert rejected.value.status_code == 401
    expected = ("Token does not contain a user identity" if guard.__module__ == monitoring.__name__
                else "A verified Firebase identity is required")
    assert rejected.value.detail == expected


@pytest.mark.parametrize("module", [monitoring, media])
def test_role_guards_preserve_responses(module):
    farmer = {"uid": "farmer"}
    specialist = {"uid": "expert", "role": "expert"}
    assert module._farmer(farmer) == "farmer"
    assert module._expert(specialist) == "expert"
    for guard, decoded, message in (
        (module._expert, farmer, "Expert access required"),
        (module._farmer, specialist, "Farmer access required"),
    ):
        with pytest.raises(HTTPException) as rejected:
            guard(decoded)
        assert (rejected.value.status_code, rejected.value.detail) == (403, message)
