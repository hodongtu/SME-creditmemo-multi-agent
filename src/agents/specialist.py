"""Specialist agents + credit-memo composer (extracted from the notebook)."""

from __future__ import annotations

import re
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, PromptTemplate

from src.utils.paths import PROJECT_ROOT
from src.types import truncate_text

class SpecialistAgent:
    """Base wrapper for specialist direct chains or tool agents."""

    name = "specialist_agent"
    template_relative_path = ""
    intro = ""

    def __init__(self, llm: Any, tools: list[Any] | None = None):
        self.llm = llm
        self.tools = tools or []
        template_path = PROJECT_ROOT / self.template_relative_path
        self.output_template = (
            template_path.read_text(encoding="utf-8")
            if template_path.is_file()
            else ""
        )
        self.system_prompt = f"""
        {self.intro}

        LANGUAGE RULE:
        - Always answer in the same language as the user's request.
        - If the user's request is Vietnamese, answer fully in Vietnamese.
        - If the user's request has no clear language, answer in Vietnamese by default.
        - Keep official names, account names, and technical terms in their original
        language when needed.

        MONETARY UNIT RULE:
        - Trình bày mọi giá trị tiền tệ bằng đơn vị tỷ VNĐ, làm tròn 2 chữ số
        thập phân, dùng dấu phẩy làm dấu thập phân và dấu chấm cho hàng nghìn
        (ví dụ: 3.991.124.661.120 VNĐ → 3.991,12 tỷ VNĐ). Không ghi số đồng thô.

        HIGHLIGHT RULE:
        - Trong mỗi mục "Nhận xét"/"Kết luận" và bất kỳ đoạn phân tích nào, in đậm
        (markdown **bold**) các điểm mấu chốt giúp người đọc nắm nhanh: số liệu/chỉ
        số bất thường hoặc biến động lớn, vi phạm ngưỡng an toàn, dấu hiệu rủi ro,
        và câu kết luận/khuyến nghị chính.
        - Chỉ in đậm phần thực sự quan trọng (vài từ hoặc một số liệu cụ thể), không
        in đậm cả câu hoặc cả đoạn — in đậm quá nhiều sẽ mất tác dụng nhấn mạnh.
        - Với cảnh báo rủi ro nghiêm trọng cần người đọc chú ý ngay, có thể dùng thêm
        blockquote (> ...). Không dùng emoji hoặc icon.

        CITATION RULE:
        - Ngay sau mỗi điểm đã in đậm theo HIGHLIGHT RULE, thêm citation nguồn theo
        định dạng đơn giản: [tên file, trang X] (PDF) hoặc [tên file, Sheet Y]
        (Excel/CSV nhiều sheet). Nếu không xác định được trang/sheet cụ thể, chỉ
        ghi [tên file].
        - Lấy "tên file" từ dòng "Document filename:" của tài liệu tương ứng trong
        evidence. Lấy số trang/tên sheet từ các mốc "--- Page N ---" hoặc
        "--- Sheet: ... ---" gần nhất với dữ liệu đó trong nội dung tài liệu; nếu
        dữ liệu lấy từ khối [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT], dùng trường "page" đi kèm
        chỉ tiêu đó trong JSON.
        - Không bịa số trang hoặc tên file — chỉ trích dẫn khi thực sự xác định
        được từ nguồn được cung cấp.

        You must provide your answer in markdown format and always follow the
        markdown template below:
        {self.output_template}
        """
        self.agent = None
        self.chain = None
        self.agent_error = ""
        if self.llm and self.tools:
            try:
                self.agent = create_agent(
                    model=self.llm,
                    tools=self.tools,
                    system_prompt=self.system_prompt,
                    name=self.name,
                )
            except Exception as exc:
                self.agent_error = f"{type(exc).__name__}: {exc}"
                print(f"{self.name} create_agent failed: {self.agent_error}")
        if self.llm and self.agent is None:
            self.chain = self._build_direct_chain()

    def analyze(self, user_input: str) -> Any:
        """Run the specialist analysis."""

        if self.agent:
            return self.agent.invoke(
                {"messages": [HumanMessage(content=user_input)]}
            )
        if self.chain:
            return self.chain.invoke({"user_input": user_input})
        return AIMessage(content=self._fallback_response(user_input))

    def _build_direct_chain(self):
        """Build a single-call chain for specialists without tools."""

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.system_prompt),
                ("human", "{user_input}"),
            ]
        )
        return prompt | self.llm | StrOutputParser()

    def _fallback_response(self, user_input: str) -> str:
        """Return deterministic preview when no analysis LLM is configured."""

        return (
            f"# {self.name}\n\n"
            "LLM chưa được cấu hình, nên notebook chỉ hiển thị bản "
            "tóm tắt evidence preview.\n\n"
            "```text\n"
            f"{truncate_text(user_input, 3_000)}\n"
            "```"
        )


class BusinessActivityAnalysis(SpecialistAgent):
    """Business activity analysis specialist."""

    name = "business_activity_agent"
    template_relative_path = (
        "src/templates/business-activity-template.md"
    )
    intro = """You are an agent among a team of assistants. You are specialized 
    for assessing customer business operations and business performance.

    CORE RESPONSIBILITIES:
    - Synthesize and evaluate actual business activities, main products/services,
    revenue contribution ratio, product change trends, and market suitability.
    - Identify obsolete products or business activities focused on undeclared areas.
    - Assess raw material supply stability, dependence on suppliers, price volatility,
    logistics, and cash flow.
    - Assess product sales potential, dependence on major customers, and revenue
    sustainability.
    """


class FinancialAnalysis(SpecialistAgent):
    """Financial analysis specialist."""

    name = "financial_analysis_agent"
    template_relative_path = (
        "src/templates/financial-analysis-template.md"
    )
    intro = """You are an agent among a team of assistants. You are specialized 
    for financial analysis.

    You analyze all finance-related information for SME underwriting, including
    financial statements, detailed ledgers, VAT declarations, bank statements,
    receivables/payables, revenue, expenses, cash flow, assets, liabilities, and
    capital structure.

    CORE RESPONSIBILITIES:
    - Analyze finance-related documents and provide evidence-grounded insights.
    - Assess reliability and consistency across reports, ledgers, declarations, and
    supporting documents.
    - Analyze revenue fluctuations between reporting periods.
    - Evaluate COGS, gross margin, expenses, unusual revenue/expenses, assets,
    liabilities, equity, borrowings, payables/receivables, and capital imbalances.
    - IMPORTANT: all statements and opinions must be supported by specific evidence.
    """


class CreditRelationshipAnalysis(SpecialistAgent):
    """Credit relationship specialist using T24 and CIC database tools."""

    name = "credit_relationship_agent"
    intro = """You are an agent among a team of assistants. You are specialized
    for credit relationship analysis.

    INPUT DATA SOURCES:
    - Internal T24 credit relationship data queried by database tools.
    - CIC/bureau credit data queried by database tools.

    CORE RESPONSIBILITIES:
    - Assess current outstanding balance, credit limits, facility types, maturity,
    repayment status, overdue status, and utilization.
    - Compare internal T24 information with CIC/bureau information.
    - Identify warning signals such as overdue debt, high leverage across banks,
    multiple concurrent facilities, restructuring, or inconsistent bureau records.
    - Use LLM only to comment and evaluate. Do not invent credit relationship data.
    - Clearly state when T24 or CIC data is unavailable from tools.
    """


class RiskAssessment(SpecialistAgent):
    """Credit risk assessment specialist."""

    name = "risk_assessment_agent"
    template_relative_path = (
        "src/templates/risk-assessment-template.md"
    )
    intro = """You are an agent among a team of assistants. You are
    specialized for credit risk assessment.

    CORE RESPONSIBILITIES:
    - Summarize red flags from business operations and financial analysis. All risks
    require specific evidence.
    - Rank risk level as high/medium/low based on repayment capacity and loan size.
    - List the top 5 biggest risks and propose risk mitigation measures.
    - Assess risks from business activity, credit relationship, financial analysis,
    and credit proposal outputs.
    """


def calculate_credit_proposal(
    input_text: str,
    business_analysis: str,
    credit_relationship_analysis: str,
    financial_analysis: str,
    documents: list[dict[str, Any]] | None = None,
) -> str:
    """Placeholder calculator for the Credit Proposal step."""

    def bool_vi(value: bool) -> str:
        """Render booleans in Vietnamese for the memo output."""

        return "Có" if value else "Không"

    return "\n".join(
        [
            "## Credit Proposal",
            "",
            "Tạm thời chưa cấu hình calculator đề xuất cấp tín dụng.",
            "",
            "### Tóm tắt đầu vào",
            f"- Yêu cầu người dùng: {input_text}",
            "- Có phân tích hoạt động kinh doanh: "
            f"{bool_vi(bool(business_analysis.strip()))}",
            "- Có phân tích quan hệ tín dụng: "
            f"{bool_vi(bool(credit_relationship_analysis.strip()))}",
            "- Có phân tích tài chính: "
            f"{bool_vi(bool(financial_analysis.strip()))}",
            f"- Số lượng tài liệu: {len(documents or [])}",
        ]
    )


def build_credit_memo(
    business_analysis: str,
    credit_relationship_analysis: str,
    financial_analysis: str,
    credit_proposal: str,
    risk_assessment: str,
) -> str:
    """Compose final Credit Memo deterministically as a safe fallback."""

    sections = [
        "# Báo cáo thẩm định tín dụng",
        "",
        "## 1. Phân tích hoạt động kinh doanh",
        business_analysis.strip() or "Chưa có kết quả phân tích hoạt động.",
        "",
        "## 2. Quan hệ tín dụng",
        credit_relationship_analysis.strip()
        or "Chưa có kết quả phân tích quan hệ tín dụng.",
        "",
        "## 3. Phân tích tài chính",
        financial_analysis.strip() or "Chưa có kết quả phân tích tài chính.",
        "",
        "## 4. Đề xuất tín dụng",
        credit_proposal.strip() or "Chưa có kết quả đề xuất cấp tín dụng.",
        "",
        "## 5. Đánh giá rủi ro",
        risk_assessment.strip() or "Chưa có kết quả đánh giá rủi ro.",
    ]
    return "\n\n".join(sections)


class CreditMemoComposerAgent:
    """Small LLM agent that edits sub-agent outputs into one clean memo."""

    def __init__(self, llm: Any, max_input_chars: int = 80_000):
        self.llm = llm
        self.max_input_chars = max_input_chars
        self.chain = self._build_chain() if self._has_configured_llm() else None

    def compose(
        self,
        input_text: str,
        business_analysis: str,
        credit_relationship_analysis: str,
        financial_analysis: str,
        credit_proposal: str,
        risk_assessment: str,
    ) -> str:
        """Create a polished Credit Memo without changing source facts."""

        fallback = build_credit_memo(
            business_analysis,
            credit_relationship_analysis,
            financial_analysis,
            credit_proposal,
            risk_assessment,
        )
        if not self.chain:
            return self._post_process_memo(fallback)

        try:
            response = self.chain.invoke(
                {
                    "input_text": truncate_text(input_text, 2_000),
                    "business_analysis": self._clean_section(
                        business_analysis
                    ),
                    "credit_relationship_analysis": self._clean_section(
                        credit_relationship_analysis
                    ),
                    "financial_analysis": self._clean_section(
                        financial_analysis
                    ),
                    "credit_proposal": self._clean_section(credit_proposal),
                    "risk_assessment": self._clean_section(risk_assessment),
                }
            )
            return self._post_process_memo(response.strip() or fallback)
        except Exception as exc:
            print(f"Credit Memo composer LLM failed: {exc}")
            return self._post_process_memo(fallback)

    def _build_chain(self):
        """Build the prompt chain for the memo composer."""

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    """
                    Bạn là CREDIT_MEMO_COMPOSER_AGENT cho quy trình thẩm định
                    tín dụng SME. Nhiệm vụ của bạn chỉ là biên tập và ghép
                    các kết quả đã có thành một báo cáo thống nhất.

                    Chỉ sử dụng output của các sub-agent được cung cấp. Không
                    thêm sự kiện, số liệu, doanh nghiệp, kết luận, xếp hạng rủi
                    ro hoặc khuyến nghị mới. Luôn giữ lại các giới hạn dữ liệu
                    và ghi chú thiếu bằng chứng quan trọng.

                    Toàn bộ báo cáo phải viết bằng Tiếng Việt, bao gồm heading,
                    nhãn bảng, chú thích và nhận xét. Chỉ giữ nguyên tên riêng,
                    mã hệ thống hoặc thuật ngữ chính thức như T24, CIC, OEM,
                    AASC nếu đó là dữ kiện nguồn.

                    Tạo một markdown document sạch với cấu trúc cấp cao sau:

                    # Báo cáo thẩm định tín dụng

                    ## 1. Thông tin khách hàng
                        - **Tên khách hàng**: [CompanyName]
                        - **Mã số thuế**: [TaxCode]
                        - **Số đăng ký kinh doanh**: [RegistrationNumber]
                        - **Ngành nghề kinh doanh**: [GSO Sector > Industry]
                    ## 2. Phân tích hoạt động kinh doanh
                    ## 3. Quan hệ tín dụng
                    ## 4. Phân tích tài chính
                    ## 5. Đề xuất tín dụng
                    ## 6. Đánh giá rủi ro

                    Quy tắc heading và đánh số:
                    - Chỉ dùng đúng một H1: # Báo cáo thẩm định tín dụng.
                    - Tất cả heading từ H2 trở xuống phải có số thứ tự đa cấp.
                    - H2 dùng dạng: ## 1. Tên mục, ## 2. Tên mục.
                    - H3 dùng dạng: ### 1.1. Tên mục, ### 1.2. Tên mục.
                    - H4 dùng dạng: #### 1.1.1. Tên mục khi thật sự cần.
                    - Nếu có H5/H6, tiếp tục cùng quy tắc 1.1.1.1.
                    - Không dùng lại heading tiếng Anh như Credit Memo,
                      Customer Info, Business Activity Analysis, Financial
                      Analysis, Credit Proposal, Risk Assessment.
                    - Chuyển mọi H1/H2 bên trong output nguồn thành H3 hoặc
                      thấp hơn dưới đúng mục cha.
                    - Xóa tiêu đề báo cáo trùng lặp, tên agent, wrapper heading
                      và các chuỗi đánh số cũ gây xung đột.

                    Quy tắc nội dung:
                    - Trình bày mọi giá trị tiền tệ bằng đơn vị tỷ VNĐ, làm
                      tròn 2 chữ số thập phân, dấu phẩy là dấu thập phân
                      (ví dụ 3.991.124.661.120 VNĐ → 3.991,12 tỷ VNĐ).
                    - Giữ nguyên bảng markdown và bullet quan trọng khi hữu ích.
                    - Giữ nguyên nội dung nguồn, không chỉnh sửa, loại bỏ nội dung.
                    - Giữ nguyên các đoạn in đậm (**bold**) đánh dấu điểm mấu chốt
                      (số liệu bất thường, vi phạm ngưỡng, dấu hiệu rủi ro, kết
                      luận chính) từ các sub-agent — không xóa bỏ định dạng nhấn
                      mạnh này khi biên tập lại heading/đánh số.
                    - Giữ nguyên các citation nguồn dạng [tên file, trang X] hoặc
                      [tên file, Sheet Y] đi kèm các điểm in đậm — không xóa bỏ
                      hoặc chỉnh sửa citation khi biên tập lại nội dung.
                    """,
                ),
                (
                    "human",
                    """
                    YÊU CẦU NGƯỜI DÙNG:
                    {input_text}

                    PHÂN TÍCH HOẠT ĐỘNG KINH DOANH:
                    {business_analysis}

                    PHÂN TÍCH QUAN HỆ TÍN DỤNG:
                    {credit_relationship_analysis}

                    PHÂN TÍCH TÀI CHÍNH:
                    {financial_analysis}

                    ĐỀ XUẤT TÍN DỤNG:
                    {credit_proposal}

                    ĐÁNH GIÁ RỦI RO:
                    {risk_assessment}
                    """,
                ),
            ]
        )
        return prompt | self.llm | StrOutputParser()

    def _clean_section(self, text: str) -> str:
        """Trim each input section before sending it to the composer."""

        return truncate_text(text.strip(), self.max_input_chars // 5)

    def _post_process_memo(self, text: str) -> str:
        """Normalize Vietnamese headings and hierarchical numbering."""

        if not text.strip():
            return text

        lines: list[str] = []
        counters = [0, 0, 0, 0, 0]
        in_code_block = False
        seen_h1 = False

        for raw_line in text.splitlines():
            if raw_line.strip().startswith("```"):
                in_code_block = not in_code_block
                lines.append(raw_line)
                continue

            if in_code_block:
                lines.append(raw_line)
                continue

            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw_line)
            if not match:
                lines.append(raw_line)
                continue

            hashes, title = match.groups()
            level = len(hashes)
            title = self._translate_heading_text(title)

            if level == 1:
                if not seen_h1:
                    lines.append("# Báo cáo thẩm định tín dụng")
                    seen_h1 = True
                else:
                    h2_number = self._next_heading_number(counters, 2)
                    lines.append(f"## {h2_number}. {title}")
                continue

            number = self._next_heading_number(counters, level)
            lines.append(f"{hashes} {number}. {title}")

        if not seen_h1:
            lines.insert(0, "")
            lines.insert(0, "# Báo cáo thẩm định tín dụng")

        return "\n".join(lines).strip()

    def _next_heading_number(
        self,
        counters: list[int],
        level: int,
    ) -> str:
        """Advance counters and return a markdown heading number."""

        index = min(max(level - 2, 0), len(counters) - 1)
        for parent_index in range(index):
            if counters[parent_index] == 0:
                counters[parent_index] = 1

        counters[index] += 1
        for reset_index in range(index + 1, len(counters)):
            counters[reset_index] = 0

        return ".".join(str(value) for value in counters[:index + 1])

    def _translate_heading_text(self, title: str) -> str:
        """Translate common composer headings to Vietnamese."""

        clean_title = self._strip_heading_number(title)
        heading_map = {
            "Credit Memo": "Báo cáo thẩm định tín dụng",
            "Customer Info": "Thông tin khách hàng",
            "Customer Information": "Thông tin khách hàng",
            "Business Activity Analysis": "Phân tích hoạt động kinh doanh",
            "Credit Relationship": "Quan hệ tín dụng",
            "Credit Relationship Analysis": "Quan hệ tín dụng",
            "Financial Analysis": "Phân tích tài chính",
            "Credit Proposal": "Đề xuất tín dụng",
            "Risk Assessment": "Đánh giá rủi ro",
            "Input summary": "Tóm tắt đầu vào",
            "Input Summary": "Tóm tắt đầu vào",
            "Output": "Đầu ra",
            "Input": "Đầu vào",
            "Red flags": "Dấu hiệu cảnh báo rủi ro",
            "Red Flags": "Dấu hiệu cảnh báo rủi ro",
            "Red flags chính": "Dấu hiệu cảnh báo rủi ro chính",
            "Red flags và bằng chứng cụ thể": (
                "Dấu hiệu cảnh báo rủi ro và bằng chứng cụ thể"
            ),
            "Top 5 rủi ro lớn nhất và biện pháp giảm thiểu": (
                "5 rủi ro lớn nhất và biện pháp giảm thiểu"
            ),
        }
        return heading_map.get(clean_title, clean_title)

    def _strip_heading_number(self, title: str) -> str:
        """Remove existing numeric prefixes before renumbering headings."""

        clean_title = title.strip().strip("#").strip()
        clean_title = clean_title.strip("*` _")
        return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", clean_title).strip()

    def _has_configured_llm(self) -> bool:
        """Return True when the dedicated composer LLM has a model name."""

        if not self.llm:
            return False
        
        model = getattr(self.llm, "model_name", "") or getattr(
            self.llm,
            "model",
            "",
        )
        return bool(str(model).strip())

