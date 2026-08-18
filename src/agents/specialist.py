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
    # Layout only (headings + empty tables). Never contains instructions: the
    # model reproduces this file, so anything written here reaches the reader.
    structure_relative_path = ""
    # How to analyse. Goes into the rules area, never into the output.
    guidance_relative_path = ""
    intro = ""
    require_citations = True

    @staticmethod
    def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
        """Separate Agent-Skills style YAML frontmatter from the body.

        Guidance files carry ``name``/``description`` per the Agent Skills
        spec so they can be moved to a skills runtime later without rewriting.
        The metadata is for humans and future tooling — it must never reach the
        model, or it ends up copied into the report like any other scaffolding.

        Only the flat ``key: value`` and folded ``key: >-`` forms used by the
        spec are handled, so no YAML dependency is required.
        """

        if not text.startswith("---"):
            return {}, text
        parts = text.split("---", 2)
        if len(parts) < 3:
            return {}, text  

        metadata: dict[str, str] = {}
        key = ""
        for line in parts[1].splitlines():
            if not line.strip():
                continue
            if not line.startswith((" ", "\t")) and ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                metadata[key] = "" if value in {">-", ">", "|", "|-"} else value
            elif key:
                metadata[key] = f"{metadata[key]} {line.strip()}".strip()
        return metadata, parts[2].lstrip("\n")

    @classmethod
    def _guidance_metadata(cls) -> dict[str, str]:
        """Frontmatter of this agent's guidance file (name/description)."""

        if not cls.guidance_relative_path:
            return {}
        path = PROJECT_ROOT / cls.guidance_relative_path
        if not path.is_file():
            return {}
        metadata, _ = cls._split_frontmatter(path.read_text(encoding="utf-8"))
        return metadata

    @classmethod
    def _read_template(cls, relative_path: str) -> str:
        """Read a template file, dropping any frontmatter."""

        if not relative_path:
            return ""
        path = PROJECT_ROOT / relative_path
        if not path.is_file():
            return ""
        _, body = cls._split_frontmatter(path.read_text(encoding="utf-8"))
        return body

    def __init__(self, llm: Any, tools: list[Any] | None = None):
        self.llm = llm
        self.tools = tools or []
        self.output_template = self._read_template(self.structure_relative_path)
        self.analysis_guidance = self._read_template(self.guidance_relative_path)
        # Skill-style metadata: not sent to the model, kept for tooling/logging.
        self.guidance_metadata = self._guidance_metadata()
        # Applied only to agents with require_citations = True. The financial agent opts
        # out: its figures all come from the same structured BCTC block, so a per-number
        # file/page citation is repetitive and crowds already-dense tables.
        self.CITATION_RULE = """CITATION RULE:
        - Trích dẫn nguồn bằng CHÚ THÍCH CUỐI BÀI. Trong câu, chỉ đặt mã số dạng
        [^1], [^2] ngay sau dữ kiện (trước dấu phẩy/dấu chấm). Nguồn đầy đủ ghi
        ở khối định nghĩa cuối phần bạn viết. TUYỆT ĐỐI không viết tên file
        thẳng vào câu như [tên file, trang X] — chỉ mã số.
        - CHỈ trích dẫn những DỮ KIỆN MẤU CHỐT, không trích mọi con số. Mấu chốt
        là các điểm bạn đã in đậm theo HIGHLIGHT RULE: số liệu bất thường, biến
        động lớn, vi phạm ngưỡng, dấu hiệu rủi ro. Mật độ hợp lý là KHOẢNG 1-2
        mã mỗi mục. Trích dẫn dày đặc từng mệnh đề làm báo cáo rối và danh sách
        nguồn dài vô ích.
        - NHƯNG chỉ gắn mã vào phần in đậm là DỮ KIỆN ĐỌC ĐƯỢC TỪ HỒ SƠ. Phần
        in đậm là KẾT LUẬN/KHUYẾN NGHỊ của bạn thì KHÔNG gắn mã — hồ sơ không
        ghi sẵn kết luận đó, gắn mã vào sẽ khiến người đọc tưởng ngược lại.
        - Hai dữ kiện cùng một nguồn thì DÙNG LẠI cùng một mã, không tạo mã mới.
        - Đánh số [^1], [^2], [^3]... tăng dần liên tục trong TOÀN BỘ phần bạn
        viết, không quay lại [^1] ở mỗi mục.
        - Lấy "tên file" từ dòng "Document filename:" của tài liệu tương ứng trong
        evidence. Lấy số trang/tên sheet từ các mốc "--- Page N ---" hoặc
        "--- Sheet: ... ---" gần nhất với dữ liệu đó trong nội dung tài liệu; nếu
        dữ liệu lấy từ khối [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT], dùng trường "page" của
        chỉ tiêu đó trong JSON, và nếu chỉ tiêu không có "page" thì dùng "page"
        của bảng chứa nó (balance_sheet/income_statement/cash_flow_statement).
        - Với số lấy từ khối [PRE-COMPUTED FINANCIAL METRICS]: nguồn là TÊN FILE
        ghi ở mục "NGUỒN SỐ LIỆU" của khối đó, theo đúng năm của con số.
        - TUYỆT ĐỐI không ghi tên khối kỹ thuật vào báo cáo. Các chuỗi
        [PRE-COMPUTED FINANCIAL METRICS], [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT],
        [DỮ LIỆU ĐỀ NGHỊ CẤP TÍN DỤNG] chỉ là nhãn nội bộ trong prompt; người đọc
        báo cáo không biết chúng là gì. Luôn trích dẫn tài liệu gốc.
        - Không bịa số trang, tên file hay mã số — chỉ trích dẫn khi thực sự xác
        định được từ nguồn được cung cấp. Mọi mã [^N] dùng trong bài BẮT BUỘC
        phải có dòng định nghĩa tương ứng.
        - Kết thúc phần bạn viết bằng ĐÚNG MỘT khối định nghĩa nguồn, đặt sau
        cùng, cách nội dung phía trên bằng một dòng trống, một dòng "---", rồi
        một dòng trống nữa. Mỗi mã một dòng, theo thứ tự số tăng dần:

          ---

          [^1]: <tên file>, trang X
          [^2]: <tên file>, Sheet Y
          [^3]: <tên file>

        Nếu không xác định được trang/sheet thì chỉ ghi tên file. Không lặp lại
        khối này ở giữa bài.

        Ví dụ mật độ ĐÚNG (một mã cho dữ kiện chính, không rải khắp câu),
        trình bày theo đúng khuôn gạch đầu dòng của NHẬN ĐỊNH RULE:

          **Nhận định**:

          - Dư nợ đạt đỉnh **56,07 tỷ VNĐ tại 07/2025**[^1] rồi giảm liên tục
            xuống 35,94 tỷ VNĐ tại 02/2026, cho thấy khách hàng đang thu hẹp
            quy mô vay.
          - **Cần theo dõi nguyên nhân thu hẹp quy mô** trước khi cấp hạn mức
            mới.

        (Cụm "cho thấy..." và gạch đầu dòng khuyến nghị in đậm là SUY LUẬN của
        bạn nên không mang mã.)
        """
        self.system_prompt = f"""
        {self.intro}

        LANGUAGE RULE:
        - Always write the whole report in Vietnamese, regardless of the language
        of the user's request. The report template, the required wordings
        ("Không có dữ liệu trong hồ sơ", "**Nhận định**") and the downstream
        checks are all Vietnamese, so an English answer would break them.
        - If the user writes in another language, still answer in Vietnamese; you
        may restate their question in Vietnamese first.
        - Keep official names, system codes (T24, CIC, AASC), account names and
        technical terms in their original form — do not translate them.

        MONETARY UNIT RULE:
        - Trình bày mọi giá trị tiền tệ bằng đơn vị tỷ VNĐ, làm tròn 2 chữ số
        thập phân, dùng dấu phẩy làm dấu thập phân và dấu chấm cho hàng nghìn
        (ví dụ: 3.991.124.661.120 VNĐ → 3.991,12 tỷ VNĐ). Không ghi số đồng thô.

        EVIDENCE RULE (quan trọng nhất — ưu tiên cao hơn việc điền đủ mẫu báo cáo):
        - Mọi con số và mọi nhận định phải truy được về bằng chứng đã cung cấp
        (nội dung tài liệu, khối [DỮ LIỆU BCTC ĐÃ TRÍCH XUẤT], khối
        [PRE-COMPUTED FINANCIAL METRICS], hoặc kết quả từ tool). Tuyệt đối không
        dùng kiến thức bên ngoài hồ sơ.
        - Khi một số liệu hoặc thông tin KHÔNG có trong hồ sơ, ghi đúng chuỗi
        "Không có dữ liệu trong hồ sơ". Không ước lượng, không suy đoán, không
        điền 0 hay "-" để lấp chỗ trống.
        - Không lặp lại giá trị của kỳ này sang kỳ khác để điền cho đủ ô. Nếu chỉ
        có số liệu của một kỳ, chỉ trình bày kỳ đó.
        - Với số liệu tự tính (tỷ trọng, tăng trưởng, chỉ số), nêu rõ các số đầu
        vào đã dùng để tính. Nếu thiếu đầu vào thì không được tự tính.
        - Không khẳng định thông tin về pháp lý, ngành nghề, thị phần, vị thế
        cạnh tranh, đăng ký kinh doanh hay quan hệ đối tác nếu không có trong
        tài liệu được cung cấp.
        - Thà báo thiếu dữ liệu còn hơn đưa ra một nhận định không có căn cứ.

        NHẬN ĐỊNH RULE (bắt buộc, áp dụng cho MỌI mục "Nhận xét", "Kết luận",
        "Đánh giá"):
        - Mỗi mục có nhãn **Nhận định**: theo sau là DANH SÁCH GẠCH ĐẦU DÒNG,
        KHÔNG viết thành một đoạn văn liền mạch. Tối đa 5 gạch đầu dòng; mỗi
        gạch đầu dòng dài 1-2 câu (khoảng 30-40 từ), nêu ĐÚNG MỘT ý — không
        dồn nhiều dữ kiện rời rạc vào một gạch đầu dòng cho đủ chỉ tiêu, và
        không viết thêm gạch đầu dòng chỉ để lấp cho đủ 5.
        - BẮT BUỘC có một dòng trống giữa dòng nhãn "**Nhận định**:" và gạch
        đầu dòng đầu tiên. Thiếu dòng trống này, trình markdown sẽ không nhận
        ra đây là danh sách và hiển thị sai (dấu "-" bị nuốt vào câu văn phía
        trên, mất bullet).
        - Ngoại lệ: nếu mục chỉ có ĐÚNG MỘT ý ngắn để nói (ví dụ báo thiếu dữ
        liệu), viết thẳng trên cùng dòng nhãn, KHÔNG tách thành bullet đơn lẻ:
        **Nhận định**: Không có dữ liệu trong hồ sơ.
        - Trong mỗi gạch đầu dòng, có thể gồm cả dữ kiện đọc được từ hồ sơ lẫn
        phần suy luận/đánh giá của bạn, viết nối tiếp nhau nếu chúng thuộc
        cùng một ý. Người đọc vẫn phải phân biệt được đâu là dữ kiện đâu là
        suy luận, bằng CÁCH DÙNG TỪ: dữ kiện nêu thẳng số liệu/thông tin đọc
        được; suy luận dùng các cụm "cho thấy", "chứng tỏ", "phản ánh", "điều
        này", "có thể", "dự kiến", "nhiều khả năng", "cần theo dõi" để báo cho
        người đọc biết đây là ý kiến của bạn, không phải nguyên văn hồ sơ.
        - TUYỆT ĐỐI không gắn mã chú thích [^N] vào phần suy luận của chính bạn —
        mã chỉ đi kèm dữ kiện đọc trực tiếp từ hồ sơ. Gắn vào suy luận sẽ khiến
        người đọc tưởng suy luận đó cũng được ghi sẵn trong hồ sơ nguồn.
        - Không quy kết nguyên nhân hay đánh giá tốt/xấu như thể đó là dữ kiện.
        Nếu chưa đủ cơ sở để suy luận, vẫn ghi gạch đầu dòng với phần dữ kiện
        đọc được và nêu rõ "Chưa đủ cơ sở để đánh giá".
        - Nếu không có dữ kiện nào cho mục này, ghi
        **Nhận định**: Không có dữ liệu trong hồ sơ.

        Ví dụ ĐÚNG (dòng trống bắt buộc trước gạch đầu dòng đầu tiên; nếu mục
        của bạn dùng CITATION RULE bên dưới thì gắn mã [^N] vào dữ kiện như ví
        dụ ở CITATION RULE):

          **Nhận định**:

          - Doanh thu thuần **tăng 71,8%** so với cùng kỳ, chủ yếu nhờ mở
            rộng thị trường xuất khẩu.
          - Biên lợi nhuận gộp **giảm còn 8,2%**, cho thấy áp lực chi phí đầu
            vào đang lấn át phần tăng trưởng doanh thu.

        HIGHLIGHT RULE:
        - Trong mỗi mục "Nhận xét"/"Kết luận" và bất kỳ đoạn phân tích nào, in đậm
        (markdown **bold**) các điểm mấu chốt giúp người đọc nắm nhanh: số liệu/chỉ
        số bất thường hoặc biến động lớn, vi phạm ngưỡng an toàn, dấu hiệu rủi ro,
        và câu kết luận/khuyến nghị chính.
        - Chỉ in đậm phần thực sự quan trọng (vài từ hoặc một số liệu cụ thể), không
        in đậm cả câu hoặc cả đoạn — in đậm quá nhiều sẽ mất tác dụng nhấn mạnh.
        - Với cảnh báo rủi ro nghiêm trọng cần người đọc chú ý ngay, có thể dùng thêm
        blockquote (> ...). Không dùng emoji hoặc icon.

        {self.CITATION_RULE if self.require_citations else ""}
        HƯỚNG DẪN PHÂN TÍCH (chỉ dẫn nội bộ — không được chép vào báo cáo):
        {self.analysis_guidance}

        BỐ CỤC BÁO CÁO:
        - Trả lời bằng markdown, bám theo bố cục dưới đây.
        - Bố cục chỉ gồm tiêu đề và khung bảng rỗng. Nhiệm vụ của bạn là ĐIỀN nội
        dung vào, không phải chép lại khung.
        - Mọi chỗ dạng {{{{TenTruong}}}} là chỗ cần thay bằng giá trị thật lấy từ
        hồ sơ. Nếu không có giá trị, ghi "Không có dữ liệu".
        TUYỆT ĐỐI không để lại dấu {{{{ }}}} trong câu trả lời.
        - Trường "Hồ sơ"/"Nguồn dữ liệu" ở đầu báo cáo: danh sách đã được hệ
        thống lập sẵn trong khối [DANH SÁCH NGUỒN — CHÉP NGUYÊN VĂN] ở phần
        dữ liệu. CHÉP ĐÚNG các dòng đó, mỗi dòng là một dòng con. TUYỆT ĐỐI
        không tự gom, không tự rút gọn, không tự thêm bớt dòng — việc gom nhóm
        và rút gọn kỳ đã do hệ thống tính, tự làm lại sẽ ra số tài liệu sai.
        Mỗi dòng con thụt vào ĐÚNG BỐN dấu
        cách so với dấu "-" của dòng "Hồ sơ" phía trên — ít hơn bốn dấu cách,
        trình markdown sẽ không nhận là danh sách con và hiển thị phẳng ra
        ngoài, sai với khung mẫu đã cho.
        - Ưu tiên trình bày bằng gạch đầu dòng khi một chỗ trong bố cục cần liệt
        kê nhiều mục cùng loại (nhiều tài liệu, nhiều khoản mục, nhiều điều
        kiện...), thay vì gộp chung thành một câu văn cách nhau bằng dấu phẩy.
        Đoạn **Nhận định** áp dụng đúng khuôn bullet quy định riêng ở NHẬN
        ĐỊNH RULE (tối đa 5 gạch đầu dòng, có dòng trống bắt buộc trước gạch
        đầu dòng đầu tiên).
        - Xoá hẳn dòng/bảng/mục không có dữ liệu thay vì để trống hoặc điền "-".

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
    structure_relative_path = "src/templates/business-activity-structure.md"
    guidance_relative_path = "src/templates/business-activity-guidance.md"
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
    # Its figures all trace to the same structured BCTC block, so per-number
    # file/page citations only add noise to already-dense financial tables.
    require_citations = False
    structure_relative_path = "src/templates/financial-analysis-structure.md"
    guidance_relative_path = "src/templates/financial-analysis-guidance.md"
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
    structure_relative_path = "src/templates/credit-relationship-structure.md"
    guidance_relative_path = "src/templates/credit-relationship-guidance.md"
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


class CreditProposalAnalysis(SpecialistAgent):
    """Credit proposal specialist."""

    name = "credit_proposal_agent"
    structure_relative_path = "src/templates/credit-proposal-structure.md"
    guidance_relative_path = "src/templates/credit-proposal-guidance.md"
    intro = """You are an agent among a team of assistants. You are specialized
    for drafting the credit proposal.

    CORE RESPONSIBILITIES:
    - Capture the customer's stated funding need, purpose and requested amount from
    the supplied documents.
    - Propose facility type, limit, tenor, disbursement and repayment method, tied to
    the financial capacity evidenced in the documents.
    - Identify the repayment sources and the collateral offered, with the basis for
    each valuation.
    - State the credit conditions that follow from the risks visible in the file.
    - This is a PROPOSAL, not an approval. Never invent a limit, a valuation or a
    repayment source that the documents do not support.
    """


class RiskAssessment(SpecialistAgent):
    """Credit risk assessment specialist."""

    name = "risk_assessment_agent"
    structure_relative_path = "src/templates/risk-assessment-structure.md"
    guidance_relative_path = "src/templates/risk-assessment-guidance.md"
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
                    - Giữ NGUYÊN VĂN mọi mã chú thích dạng [^nhãn] trong bài,
                      kể cả khi nhãn trông lạ như [^ba1], [^cr3]: đó là quy ước
                      nội bộ để tránh trùng số giữa các agent khi ghép báo cáo.
                      TUYỆT ĐỐI không "làm gọn" chúng thành [^1], [^2], không tự
                      đánh số lại, không xóa.
                    - Giữ nguyên các dòng định nghĩa nguồn ("[^nhãn]: nội dung")
                      của từng sub-agent, đặt ngay dưới phần của sub-agent đó.
                      KHÔNG cần tự gộp thành một danh sách chung — hệ thống sẽ
                      tự gom sau khi bạn biên tập xong. Không bịa thêm và không
                      xóa bớt dòng định nghĩa nào.
                    - Giữ nguyên cấu trúc gạch đầu dòng của đoạn **Nhận định**
                      từ sub-agent — không gộp các gạch đầu dòng lại thành một
                      đoạn văn liền mạch, không thêm/bớt gạch đầu dòng, không
                      đổi thứ tự, không thêm dòng "Căn cứ" mới, không chuyển
                      câu suy luận thành câu dữ kiện hay ngược lại.
                    - Giữ nguyên mọi ghi chú thiếu dữ liệu từ sub-agent, đúng
                      nguyên văn "Không có dữ liệu trong hồ sơ".
                      Không được thay bằng số ước lượng, số 0, dấu "-", hay câu
                      văn làm mờ việc thiếu dữ liệu. Không tự thêm bất kỳ số
                      liệu hay nhận định nào không có trong output sub-agent.
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

