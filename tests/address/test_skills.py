"""Parity + well-formedness tests for the three Address skills.

P3-3: Tests that authored skill folders match corpus, contract models, and code
constants so they cannot drift.
"""
from agents.address.gate import DOCTYPE_GOV_ID, DOCTYPE_UTILITY_BILL, is_satisfied
from bridge.classification import canonicalize_issuer
from bridge.disposition import DEFAULT_THRESHOLDS
from bridge.edge.skills import loaded_skills
from contract.enums import Disposition
from contract.extraction import GovIdKeyFields, UtilityBillKeyFields
from contract.wire import CollectionStatus, LedgerEntry, RequirementsList
from tests.address._corpus import corpus_records, load_timeline
from tests.address._skills import load_skill


class TestWellFormedness:
    """Well-formedness tests for all three skills."""

    def test_gov_id_well_formed(self):
        """Test gov-id skill is well-formed."""
        skill = load_skill("gov-id")
        fm = skill["frontmatter"]

        assert skill["name"] == "gov-id", "dir name must match frontmatter name"
        assert fm["name"] == "gov-id", "name must be lowercase-hyphen"
        assert len(fm["name"]) <= 64, "name must be <= 64 chars"
        assert len(fm["description"]) <= 1024, "description must be <= 1024 chars"
        assert skill["metadata"]["bridge-kind"] == "doctype", "gov-id must be a doctype skill"
        assert skill["body"], "SKILL.md body must be non-empty"

    def test_utility_bill_well_formed(self):
        """Test utility-bill skill is well-formed."""
        skill = load_skill("utility-bill")
        fm = skill["frontmatter"]

        assert skill["name"] == "utility-bill", "dir name must match frontmatter name"
        assert fm["name"] == "utility-bill", "name must be lowercase-hyphen"
        assert len(fm["name"]) <= 64, "name must be <= 64 chars"
        assert len(fm["description"]) <= 1024, "description must be <= 1024 chars"
        assert skill["metadata"]["bridge-kind"] == "doctype", "utility-bill must be a doctype skill"
        assert skill["body"], "SKILL.md body must be non-empty"

    def test_address_proof_well_formed(self):
        """Test address-proof skill is well-formed."""
        skill = load_skill("address-proof")
        fm = skill["frontmatter"]

        assert skill["name"] == "address-proof", "dir name must match frontmatter name"
        assert fm["name"] == "address-proof", "name must be lowercase-hyphen"
        assert len(fm["name"]) <= 64, "name must be <= 64 chars"
        assert len(fm["description"]) <= 1024, "description must be <= 1024 chars"
        assert skill["metadata"]["bridge-kind"] == "process", "address-proof must be a process skill"
        assert skill["body"], "SKILL.md body must be non-empty"


class TestAddressProofSkill:
    """SKILL-1: address-proof process skill tests."""

    def test_bridge_kind_and_pattern(self):
        """Test address-proof is a process skill with collect pattern."""
        skill = load_skill("address-proof")
        meta = skill["metadata"]

        assert meta["bridge-kind"] == "process"
        assert meta["bridge-pattern"] == "collect"

    def test_candidate_doctypes_match(self):
        """Test candidate doctypes match across all sources."""
        skill = load_skill("address-proof")
        meta = skill["metadata"]

        # Parse space-separated string
        candidate_str = meta["bridge-candidate-doctypes"]
        candidates_from_skill = tuple(candidate_str.split())

        # Compare to loaded_skills()
        skills = loaded_skills()
        address_proof_skill = next(s for s in skills if s.name == "address-proof")
        assert set(candidates_from_skill) == set(address_proof_skill.candidate_doctypes), \
            "skill candidate_doctypes must match loaded_skills()"

        # Compare to gate constants
        assert set(candidates_from_skill) == {DOCTYPE_GOV_ID, DOCTYPE_UTILITY_BILL}, \
            "skill candidate_doctypes must match gate constants"

    def test_satisfaction_prose_present(self):
        """Test prose satisfaction description is present and mentions key concepts."""
        skill = load_skill("address-proof")
        meta = skill["metadata"]

        satisfaction = meta.get("bridge-satisfaction", "")
        assert satisfaction, "bridge-satisfaction must be present and non-empty"

        # Check for key concepts (case-insensitive)
        satisfaction_lower = satisfaction.lower()
        assert ("id" in satisfaction_lower or "government" in satisfaction_lower), \
            "satisfaction must mention ID/government"
        assert "two" in satisfaction_lower, "satisfaction must mention 'two' bills"
        assert ("distinct" in satisfaction_lower or "different" in satisfaction_lower), \
            "satisfaction must mention distinct/different issuers"

    def test_policy_file_exists_and_loads(self):
        """Test policy.yaml is referenced and loads successfully."""
        skill = load_skill("address-proof")
        meta = skill["metadata"]

        assert meta["bridge-policy"] == "assets/policy.yaml"
        assert "policy.yaml" in skill["assets"], "policy.yaml must exist and load"

    def test_disposition_thresholds_match(self):
        """Test policy disposition thresholds match code defaults."""
        skill = load_skill("address-proof")
        policy = skill["assets"]["policy.yaml"]

        disp = policy["disposition"]
        assert disp["auto_approve_at"] == DEFAULT_THRESHOLDS.auto_approve_at, \
            "auto_approve_at must equal DEFAULT_THRESHOLDS (0.85)"
        assert disp["auto_approve_at"] == 0.85, "auto_approve_at must be 0.85"
        assert disp["resubmit_below"] == DEFAULT_THRESHOLDS.resubmit_below, \
            "resubmit_below must equal DEFAULT_THRESHOLDS (0.55)"
        assert disp["resubmit_below"] == 0.55, "resubmit_below must be 0.55"
        assert disp["max_resubmissions"] == 3, "max_resubmissions must be 3"

    def test_sla_matches_timeline(self):
        """Test policy SLA numbers match timeline.json."""
        skill = load_skill("address-proof")
        policy = skill["assets"]["policy.yaml"]
        timeline = load_timeline()

        # Timeline SLA is the same across both exchanges
        timeline_sla = timeline["exchanges"][0]["sla"]

        sla = policy["sla"]
        assert sla["deadline"] == sla["escalate_ticks"], "deadline must equal escalate_ticks"
        assert sla["escalate_ticks"] == 3, "escalate_ticks must be 3"
        assert sla["escalate_ticks"] == timeline_sla["escalate_ticks"], \
            "escalate_ticks must match timeline"

        assert sla["overdue_ticks"] == 1, "overdue_ticks must be 1"
        assert sla["overdue_ticks"] == timeline_sla["overdue_ticks"], \
            "overdue_ticks must match timeline"

        assert sla["reminder_ticks"] == 2, "reminder_ticks must be 2"
        assert sla["reminder_ticks"] == timeline_sla["reminder_ticks"], \
            "reminder_ticks must match timeline"

        assert policy["disposition"]["max_resubmissions"] == timeline_sla["resubmit_cap"], \
            "max_resubmissions must match timeline resubmit_cap (3)"

    def test_sla_ladder(self):
        """Test SLA ladder is correct."""
        skill = load_skill("address-proof")
        policy = skill["assets"]["policy.yaml"]

        assert policy["sla"]["ladder"] == ["overdue", "reminder", "escalated"], \
            "ladder must be [overdue, reminder, escalated]"

    def test_is_satisfied_behavior(self):
        """Test that prose satisfaction matches is_satisfied code behavior.

        This pins the sense-B rule: the prose describes what is_satisfied enforces.
        """
        from contract.enums import Gate

        # One accepted gov-id -> satisfied
        status_one_govid = CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="gov-id-clean",
                    doctype="gov-id",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                    reason_code="auto_approved",
                    message="Approved",
                )
            ],
            outstanding=RequirementsList(done=True),
        )
        assert is_satisfied(status_one_govid), "one accepted gov-id must satisfy"

        # Two bills, same issuer -> NOT satisfied
        status_same_issuer = CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="bill-1",
                    doctype="utility-bill",
                    issuer="power-co",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                    reason_code="auto_approved",
                    message="Approved",
                ),
                LedgerEntry(
                    doc_id="bill-2",
                    doctype="utility-bill",
                    issuer="power-co",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                    reason_code="auto_approved",
                    message="Approved",
                ),
            ],
            outstanding=RequirementsList(done=False),
        )
        assert not is_satisfied(status_same_issuer), \
            "two bills from same issuer must NOT satisfy (distinct issuer check)"

        # Two bills, distinct issuers -> satisfied
        status_distinct_issuers = CollectionStatus(
            entries=[
                LedgerEntry(
                    doc_id="bill-powerco",
                    doctype="utility-bill",
                    issuer="power-co",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                    reason_code="auto_approved",
                    message="Approved",
                ),
                LedgerEntry(
                    doc_id="bill-aquautil",
                    doctype="utility-bill",
                    issuer="aqua-util",
                    disposition=Disposition.accepted,
                    gate=Gate.auto_approve,
                    reason_code="auto_approved",
                    message="Approved",
                ),
            ],
            outstanding=RequirementsList(done=True),
        )
        assert is_satisfied(status_distinct_issuers), \
            "two bills from distinct issuers must satisfy"


class TestGovIdSkill:
    """SKILL-2: gov-id doctype skill tests."""

    def test_extraction_metadata(self):
        """Test extraction engine and schema metadata."""
        skill = load_skill("gov-id")
        meta = skill["metadata"]

        assert meta["bridge-extraction-engine"] == "gemini"
        assert meta["bridge-schema"] == "assets/schema.json"

    def test_schema_top_level_required(self):
        """Test schema top-level structure."""
        skill = load_skill("gov-id")
        schema = skill["assets"]["schema.json"]

        assert set(schema["required"]) == {"doctype", "key_fields"}, \
            "top-level required must be {doctype, key_fields}"
        assert schema["properties"]["doctype"]["enum"] == ["gov-id"], \
            "doctype enum must be ['gov-id']"

    def test_schema_key_fields_match_contract(self):
        """Test schema key_fields match GovIdKeyFields contract model."""
        skill = load_skill("gov-id")
        schema = skill["assets"]["schema.json"]

        key_fields_schema = schema["properties"]["key_fields"]
        schema_properties = set(key_fields_schema["properties"].keys())
        schema_required = set(key_fields_schema["required"])
        contract_fields = set(GovIdKeyFields.model_fields.keys())

        expected = {"name", "address", "doc_number", "issuing_authority", "expiry"}

        assert schema_properties == expected, \
            "schema properties must match expected fields"
        assert schema_required == expected, \
            "schema required must match expected fields"
        assert schema_properties == contract_fields, \
            "schema properties must match GovIdKeyFields contract"

    def test_schema_covers_corpus_records(self):
        """Test schema covers all keys in corpus gov-id records."""
        skill = load_skill("gov-id")
        schema = skill["assets"]["schema.json"]
        schema_fields = set(schema["properties"]["key_fields"]["properties"].keys())

        records = corpus_records()
        for record in records:
            if record["extraction"]["fields"]["doctype"] == "gov-id":
                corpus_key_fields = record["extraction"]["fields"].get("key_fields", {})
                for key in corpus_key_fields:
                    assert key in schema_fields, \
                        f"corpus key '{key}' in {record['id']} must be in schema"

    def test_validation_rules(self):
        """Test validation.yaml has required rule ids."""
        skill = load_skill("gov-id")
        validation = skill["assets"]["validation.yaml"]

        rule_ids = {rule["id"] for rule in validation["rules"]}
        assert rule_ids == {"not_expired", "complete_fields"}, \
            "rule ids must be {not_expired, complete_fields}"

        assert validation["signals"]["completeness"] == "agent", \
            "completeness signal must be owned by agent"


class TestUtilityBillSkill:
    """SKILL-3: utility-bill doctype skill tests."""

    def test_extraction_metadata(self):
        """Test extraction engine and schema metadata."""
        skill = load_skill("utility-bill")
        meta = skill["metadata"]

        assert meta["bridge-extraction-engine"] == "gemini"
        assert meta["bridge-schema"] == "assets/schema.json"

    def test_schema_top_level_required(self):
        """Test schema top-level structure with issuer."""
        skill = load_skill("utility-bill")
        schema = skill["assets"]["schema.json"]

        assert set(schema["required"]) == {"doctype", "issuer", "key_fields"}, \
            "top-level required must be {doctype, issuer, key_fields}"
        assert schema["properties"]["doctype"]["enum"] == ["utility-bill"], \
            "doctype enum must be ['utility-bill']"
        assert "issuer" in schema["properties"], \
            "issuer must be a top-level property (not in key_fields)"

    def test_schema_key_fields_match_contract(self):
        """Test schema key_fields match UtilityBillKeyFields contract model."""
        skill = load_skill("utility-bill")
        schema = skill["assets"]["schema.json"]

        key_fields_schema = schema["properties"]["key_fields"]
        schema_properties = set(key_fields_schema["properties"].keys())
        schema_required = set(key_fields_schema["required"])
        contract_fields = set(UtilityBillKeyFields.model_fields.keys())

        expected = {"name", "service_address", "statement_date"}

        assert schema_properties == expected, \
            "schema properties must match expected fields"
        assert schema_required == expected, \
            "schema required must match expected fields"
        assert schema_properties == contract_fields, \
            "schema properties must match UtilityBillKeyFields contract"

    def test_schema_covers_corpus_records(self):
        """Test schema covers all keys in corpus utility-bill records.

        NOTE: bill-aquautil-blurry has no key_fields (illegible), so this is a
        subset check per record.
        """
        skill = load_skill("utility-bill")
        schema = skill["assets"]["schema.json"]
        schema_fields = set(schema["properties"]["key_fields"]["properties"].keys())

        records = corpus_records()
        for record in records:
            if record["extraction"]["fields"]["doctype"] == "utility-bill":
                corpus_key_fields = record["extraction"]["fields"].get("key_fields", {})
                for key in corpus_key_fields:
                    assert key in schema_fields, \
                        f"corpus key '{key}' in {record['id']} must be in schema"

    def test_validation_rules(self):
        """Test validation.yaml has required rules with window_days."""
        skill = load_skill("utility-bill")
        validation = skill["assets"]["validation.yaml"]

        rule_ids = {rule["id"] for rule in validation["rules"]}
        assert "recent_bill" in rule_ids, "must have recent_bill rule"
        assert "address_present" in rule_ids, "must have address_present rule"

        recent_bill_rule = next(r for r in validation["rules"] if r["id"] == "recent_bill")
        assert recent_bill_rule.get("window_days") == 90, \
            "recent_bill rule must have window_days == 90"

        assert validation["signals"]["completeness"] == "agent", \
            "completeness signal must be owned by agent"

    def test_issuer_canonicalization_reference_exists(self):
        """Test issuer-canonicalization.md exists and contains examples."""
        skill = load_skill("utility-bill")
        ref_path = skill["dir"] / "references" / "issuer-canonicalization.md"

        assert ref_path.exists(), "references/issuer-canonicalization.md must exist"

        content = ref_path.read_text()
        assert "power-co" in content.lower(), "must contain power-co example"
        assert "aqua-util" in content.lower(), "must contain aqua-util example"

    def test_canonicalization_parity(self):
        """Test documented canonicalization examples match code behavior."""
        # Test PowerCo variants
        assert canonicalize_issuer("PowerCo") == "power-co", \
            "PowerCo must canonicalize to power-co"
        assert canonicalize_issuer("Power Co.") == "power-co", \
            "Power Co. must canonicalize to power-co"
        assert canonicalize_issuer("PowerCo Ltd") == "power-co", \
            "PowerCo Ltd must canonicalize to power-co (ltd dropped, co kept)"

        # Test AquaUtil
        assert canonicalize_issuer("AquaUtil") == "aqua-util", \
            "AquaUtil must canonicalize to aqua-util"

        # Verify all PowerCo variants canonicalize identically
        assert (canonicalize_issuer("PowerCo") ==
                canonicalize_issuer("Power Co.") ==
                canonicalize_issuer("PowerCo Ltd")), \
            "all PowerCo variants must canonicalize to the same value"


class TestSkillRegistryNodrift:
    """Test that authored skills match loaded_skills() metadata (no drift)."""

    def test_all_skills_present(self):
        """Test all three skills exist in loaded_skills()."""
        skills = loaded_skills()
        skill_names = {s.name for s in skills}

        assert "address-proof" in skill_names, "address-proof must be in loaded_skills()"
        assert "gov-id" in skill_names, "gov-id must be in loaded_skills()"
        assert "utility-bill" in skill_names, "utility-bill must be in loaded_skills()"

    def test_address_proof_metadata_matches(self):
        """Test address-proof folder matches loaded_skills() metadata."""
        folder = load_skill("address-proof")
        skills = loaded_skills()
        skill_meta = next(s for s in skills if s.name == "address-proof")

        assert folder["frontmatter"]["name"] == skill_meta.name, \
            "name must match"
        assert folder["metadata"]["bridge-kind"] == skill_meta.kind, \
            "kind must match"

        # Parse candidate doctypes from skill folder
        candidates_str = folder["metadata"]["bridge-candidate-doctypes"]
        candidates = tuple(candidates_str.split())
        assert candidates == skill_meta.candidate_doctypes, \
            "candidate_doctypes must match"

    def test_gov_id_metadata_matches(self):
        """Test gov-id folder matches loaded_skills() metadata."""
        folder = load_skill("gov-id")
        skills = loaded_skills()
        skill_meta = next(s for s in skills if s.name == "gov-id")

        assert folder["frontmatter"]["name"] == skill_meta.name, \
            "name must match"
        assert folder["metadata"]["bridge-kind"] == skill_meta.kind, \
            "kind must match"

    def test_utility_bill_metadata_matches(self):
        """Test utility-bill folder matches loaded_skills() metadata."""
        folder = load_skill("utility-bill")
        skills = loaded_skills()
        skill_meta = next(s for s in skills if s.name == "utility-bill")

        assert folder["frontmatter"]["name"] == skill_meta.name, \
            "name must match"
        assert folder["metadata"]["bridge-kind"] == skill_meta.kind, \
            "kind must match"
