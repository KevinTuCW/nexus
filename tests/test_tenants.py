import pytest

from nexus.registry.tenants import TenantPolicy, load_policies, substitution_allowed


def test_loads_the_five_tenants(policies_dir):
    reg = load_policies(policies_dir)
    assert set(reg) == {"helpmate", "shopscout", "wealthwise", "aura", "wuwork"}


def test_a_model_with_no_substitutable_to_is_pinned(policies_dir):
    reg = load_policies(policies_dir)
    # shopscout's jury members are pinned: substitution would collapse the
    # very diversity the jury exists for.
    assert substitution_allowed(reg["shopscout"], "zai/glm-4.6", "qwen3") is False


def test_substitution_within_a_whitelisted_family_is_allowed(policies_dir):
    reg = load_policies(policies_dir)
    assert substitution_allowed(reg["wuwork"], "siliconflow/Qwen/Qwen3-8B", "qwen3") is True


def test_unlisted_model_defaults_to_denied(policies_dir):
    # Default-deny. Forgetting to configure a model must not hand the
    # router permission to swap it -- the failure mode of default-allow is
    # silent and only shows up as a quality regression weeks later.
    reg = load_policies(policies_dir)
    assert substitution_allowed(reg["shopscout"], "some/never-configured", "qwen3") is False


def test_wealthwise_forbids_fallback(policies_dir):
    # Gate G4: in a compliance path, silently degrading to a weaker model
    # is worse than failing loudly.
    reg = load_policies(policies_dir)
    assert reg["wealthwise"].allow_fallback is False
    assert reg["helpmate"].allow_fallback is True


def test_incumbent_tenants_declare_their_native_gate_command(policies_dir):
    # The four incumbent repos do NOT share an entrypoint -- measured, not
    # assumed: helpmate has `make gate`, shopscout and wealthwise have
    # `make eval`, aura has no eval target at all. G3's runner reads this
    # field instead of hardcoding one command.
    reg = load_policies(policies_dir)
    assert reg["helpmate"].gate_command == "make gate"
    assert reg["shopscout"].gate_command == "make eval"
    assert reg["wealthwise"].gate_command == "make eval"
    assert reg["aura"].gate_command == "make test"


def test_zero_touch_tenants_declare_a_repo_path(policies_dir):
    reg = load_policies(policies_dir)
    for name in ("helpmate", "shopscout", "wealthwise", "aura"):
        assert reg[name].integration == "zero_touch"
        assert reg[name].repo_path is not None
    # wuwork is native: it lives in this repo, so there is no external
    # checkout to keep untouched.
    assert reg["wuwork"].integration == "native"
    assert reg["wuwork"].repo_path is None


def test_cross_tenant_read_defaults_to_nothing(policies_dir):
    # Default deny. A platform whose "reuse" story depends on every tenant
    # being able to read every other tenant has not built reuse, it has
    # removed isolation and renamed the result.
    reg = load_policies(policies_dir)
    for name in ("helpmate", "shopscout", "wealthwise", "aura"):
        assert reg[name].cross_tenant_read == ()


def test_wuwork_is_the_one_authorised_crossing(policies_dir):
    reg = load_policies(policies_dir)
    assert set(reg["wuwork"].cross_tenant_read) == {
        "helpmate", "shopscout", "wealthwise", "aura"
    }


def test_a_directly_constructed_policy_grants_nothing():
    # `test_cross_tenant_read_defaults_to_nothing` looks like it pins the
    # dataclass default, but it does not: `load_policies` always passes the
    # field explicitly, so the class-level default is unreachable from that
    # path. Found by mutating the class default and watching nothing go red.
    #
    # The default is still the last line of defence for anything that builds
    # a TenantPolicy directly -- a new caller, a test fixture -- and for
    # those it is the only thing standing between a missing argument and a
    # tenant that can read everyone.
    policy = TenantPolicy(
        tenant="direct",
        integration="native",
        repo_path=None,
        gate_command="make test",
        api_key_env="NEXUS_KEY_DIRECT",
        allow_fallback=True,
        budget_nanousd_per_day=1,
    )
    assert policy.cross_tenant_read == ()
