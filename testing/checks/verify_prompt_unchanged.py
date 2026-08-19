"""Every agent's prompt must be identical, character for character, after a move.

This is the decisive check for a pure refactor: if the string handed to the LLM
does not change by one character, behaviour cannot change either. The baseline is
recorded by this same file (`--record`) before the move and compared after it.

    python testing/checks/verify_prompt_unchanged.py --record   # record baseline
    python testing/checks/verify_prompt_unchanged.py            # compare to it

The input documents are a synthetic fixture rather than customer files, so the
baseline is committable and runs on a fresh machine.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.agents.supervisor import Supervisor  # noqa: E402
from src.config import Config  # noqa: E402
from src.matrix.document_matrix import agent_relevance_for_type  # noqa: E402
from src.types import ClassifiedDocument  # noqa: E402

BASELINE = Path(__file__).resolve().parent / "fixtures" / "prompt_baseline.json"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bctc_code_collision.json"

AGENTS = (
    "BUSINESS_ACTIVITY_AGENT",
    "FINANCIAL_ANALYSIS_AGENT",
    "CREDIT_RELATIONSHIP_AGENT",
    "CREDIT_PROPOSAL_AGENT",
    "RISK_ASSESSMENT_AGENT",
)


def _documents() -> list[ClassifiedDocument]:
    """A dossier touching as many of the prompt blocks as possible."""

    fields = set(ClassifiedDocument.__dataclass_fields__)
    raw = json.load(open(FIXTURE, encoding="utf-8"))
    docs = [ClassifiedDocument(**{k: v for k, v in d.items() if k in fields}) for d in raw]
    for doc in docs:
        doc.agent_relevance = agent_relevance_for_type("bao_cao_tai_chinh", None)

    extras = [
        ("to_khai_thue_gtgt", "TKT_01.2025.xlsx", "TỜ KHAI THUẾ GTGT tháng 01/2025 " + "x" * 800),
        ("to_khai_thue_gtgt", "TKT_02.2025.xlsx", "TỜ KHAI THUẾ GTGT tháng 02/2025 " + "x" * 800),
        ("cic_khach_hang_vay", "CIC_S10A.pdf", "BÁO CÁO CIC quan hệ tín dụng " + "x" * 800),
        ("de_nghi_cap_tin_dung", "Giay_de_nghi.pdf", "GIẤY ĐỀ NGHỊ CẤP TÍN DỤNG " + "x" * 800),
        ("bao_cao_khao_sat_thuc_dia", "Khao_sat.pdf", "BÁO CÁO KHẢO SÁT THỰC ĐỊA " + "x" * 800),
        ("so_chi_tiet_khoan_muc_khac", "So_chi_tiet_331.xlsx", "SỔ CHI TIẾT TÀI KHOẢN 331 " + "x" * 800),
    ]
    for type_id, filename, content in extras:
        docs.append(
            ClassifiedDocument(
                path=f"/fixture/{filename}",
                filename=filename,
                content=content,
                agent="RISK_ASSESSMENT_AGENT",
                reasoning="fixture",
                confidence=0.9,
                document_type=type_id,
                agent_relevance=agent_relevance_for_type(type_id, None),
            )
        )
    return docs


def _prompts() -> dict[str, str]:
    supervisor = Supervisor(Config())
    docs = _documents()
    return {
        agent: supervisor._build_user_input("Phân tích hồ sơ khách hàng", docs, "", agent, "", {})
        for agent in AGENTS
    }


def main() -> int:
    prompts = _prompts()
    digests = {a: hashlib.sha256(p.encode()).hexdigest() for a, p in prompts.items()}

    if "--record" in sys.argv:
        BASELINE.write_text(
            json.dumps({"sha256": digests, "lengths": {a: len(p) for a, p in prompts.items()}},
                       ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Đã chụp mốc cho {len(digests)} agent -> {BASELINE.name}")
        for agent, digest in digests.items():
            print(f"   {agent:28} {len(prompts[agent]):>7,} ký tự  {digest[:16]}")
        return 0

    if not BASELINE.is_file():
        raise FileNotFoundError(BASELINE)

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failures = []
    for agent in AGENTS:
        want, got = baseline["sha256"].get(agent), digests[agent]
        want_len, got_len = baseline["lengths"].get(agent), len(prompts[agent])
        if want == got:
            print(f"   ✅ {agent:28} {got_len:>7,} ký tự — giống hệt")
        else:
            failures.append(agent)
            print(f"   ❌ {agent:28} {want_len:>7,} -> {got_len:>7,} ký tự — ĐÃ ĐỔI")

    print()
    if failures:
        print("Prompt đã đổi:", ", ".join(failures))
        print("Một refactor thuần tuý không được đổi chuỗi nào. Nếu thay đổi là cố ý,")
        print("chạy lại với --record và nói rõ trong commit vì sao prompt đổi.")
        return 1
    print("✅ Prompt của cả 5 agent không đổi một ký tự nào")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
