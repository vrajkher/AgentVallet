import os
import stat
import zipfile

import pytest

from agentvallet.models import SkillStatus
from agentvallet.skills import ApprovalError, IntegrityError, SkillNotFound
from agentvallet.spec import PackageError


def test_register_validate_approve(av, example_dir):
    sv = av.registry.add_from_dir(example_dir("invoice_total"))
    assert sv.version == "1.0.0" and sv.status == SkillStatus.DRAFT
    with pytest.raises(ApprovalError, match="no passing validation"):
        av.approve("invoice_total", "1.0.0", "alice")
    report = av.validate("invoice_total", "1.0.0")
    assert report.passed
    assert av.registry.get("invoice_total", "1.0.0").status == SkillStatus.VALIDATED
    with pytest.raises(ApprovalError, match="named human"):
        av.approve("invoice_total", "1.0.0", "system")
    approved = av.approve("invoice_total", "1.0.0", "alice", note="reviewed")
    assert approved.status == SkillStatus.APPROVED and approved.approved_by == "alice"
    assert not os.stat(os.path.join(approved.path, "run.py")).st_mode & stat.S_IWUSR
    actions = [e["action"] for e in av.db.audit_entries(subject="invoice_total")]
    assert "skill.approve" in actions


def test_changed_draft_requires_revalidation(av, example_dir):
    sv = av.registry.add_from_dir(example_dir("text_stats"))
    assert av.validate("text_stats", sv.version).passed
    with open(os.path.join(sv.path, "workflow.md"), "a") as fh:
        fh.write("\nedited\n")
    with pytest.raises(ApprovalError, match="changed since"):
        av.approve("text_stats", sv.version, "bob")
    assert av.validate("text_stats", sv.version).passed
    av.approve("text_stats", sv.version, "bob")


def test_tampering_with_approved_version_is_detected(av, example_dir):
    sv = av.registry.add_from_dir(example_dir("text_stats"))
    av.validate("text_stats", sv.version)
    av.approve("text_stats", sv.version, "bob")
    run_py = os.path.join(sv.path, "run.py")
    os.chmod(run_py, 0o644)
    with open(run_py, "a") as fh:
        fh.write("\n# sneaky\n")
    with pytest.raises(IntegrityError):
        av.registry.package("text_stats")
    assert not dict((c, ok) for c, ok, _ in av.doctor())["approved skills intact"]


def test_fork_diff_deprecate_discard(av, example_dir):
    av.registry.add_from_dir(example_dir("text_stats"))
    av.validate("text_stats", "1.0.0")
    av.approve("text_stats", "1.0.0", "bob")
    fork = av.registry.fork("text_stats")
    assert fork.version == "1.1.0" and fork.status == SkillStatus.DRAFT
    with open(os.path.join(fork.path, "workflow.md"), "a") as fh:
        fh.write("4. New step\n")
    av.registry.refresh_draft("text_stats", "1.1.0")
    diff = av.registry.diff("text_stats", "1.0.0", "1.1.0")
    assert "+4. New step" in diff
    av.registry.discard_draft("text_stats", "1.1.0")
    with pytest.raises(SkillNotFound):
        av.registry.get("text_stats", "1.1.0")
    with pytest.raises(ApprovalError):
        av.registry.discard_draft("text_stats", "1.0.0")
    av.registry.deprecate("text_stats", "1.0.0", "bob", "superseded")
    assert av.registry.get("text_stats", "1.0.0").status == SkillStatus.DEPRECATED


def test_export_import_roundtrip(av, example_dir, tmp_path):
    av.registry.add_from_dir(example_dir("invoice_total"))
    av.validate("invoice_total", "1.0.0")
    av.approve("invoice_total", "1.0.0", "alice")
    z = av.registry.export("invoice_total", None, tmp_path / "out")
    assert z.name == "invoice_total-1.0.0.avskill.zip"

    from agentvallet.config import Settings
    from agentvallet.core import AgentVallet

    other = AgentVallet(Settings(home=tmp_path / "other"))
    sv = other.registry.import_package(z)
    assert sv.status == SkillStatus.DRAFT  # trust is never imported
    assert other.validate("invoice_total", sv.version).passed


def test_import_rejects_tampered_and_zip_slip(av, example_dir, tmp_path):
    src = example_dir("text_stats")
    (src / "run.py").write_text("print('evil')")
    with pytest.raises(IntegrityError):
        av.registry.import_package(src)
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escape.txt", "x")
    with pytest.raises(PackageError):
        av.registry.import_package(evil)
