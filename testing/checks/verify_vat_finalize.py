"""Full _finalize integration: vat-doanh-thu block must disappear from the
final visible response, but still drive the chart's VAT line."""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv
load_dotenv(REPO / ".env", override=True)

from src.config import Config
from src.agents.supervisor import Supervisor
from src.types import ClassifiedDocument

# hallucination_llm gỡ khỏi Config ở a53f0f4.
config = Config()
supervisor = Supervisor(config)

cic_extraction = {
    "du_no_12_thang": [
        {"thang": "04/2025", "tong_du_no": 45200000000},
        {"thang": "05/2025", "tong_du_no": 48100000000},
        {"thang": "06/2025", "tong_du_no": 52300000000},
    ]
}
doc = ClassifiedDocument(
    path="/tmp/x", filename="CIC_S10A_mau.pdf", content="",
    agent="CREDIT_RELATIONSHIP_AGENT", reasoning="", confidence=0.9,
    is_cic_s10a=True, cic_s10a_extraction=cic_extraction,
)

fake_response = """## Báo cáo phân tích quan hệ tín dụng

## 2. Diễn biến dư nợ 12 tháng

**Nhận định**:

- Dư nợ tăng đều qua các tháng.

```vat-doanh-thu
04/2025: 31400000000
05/2025: 28750000000
06/2025: 35120000000
```

## 3. Tình trạng trả nợ

**Nhận định**: Không có dữ liệu trong hồ sơ.
"""

result = supervisor._finalize(
    input_text="Phân tích quan hệ tín dụng.",
    response=fake_response,
    agent_name="CREDIT_RELATIONSHIP_AGENT",
    decision={"route": "SINGLE_AGENT"},
    documents=[doc],
    sub_agent_outputs={"CREDIT_RELATIONSHIP_AGENT": fake_response},
    web_context="",
    history_context="",
    execution_plan={},
    gap_analysis={"summary": "Đủ dữ liệu."},
    steps=[],
)

final_text = result["response"]
print("=== FINAL RESPONSE ===")
print(final_text)

assert "vat-doanh-thu" not in final_text, "block leaked into final response!"
assert "```linechart" in final_text, "chart missing from final response!"
assert "Doanh thu VAT" in final_text, "VAT column missing from chart!"
assert "31,40" in final_text, "VAT figure missing from chart!"
print("\nOK: block stripped from visible response, chart built with VAT line")
