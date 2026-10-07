"""Domain exceptions (CIS §17.2). Safe messages never contain source, raw tool or model output.

The remaining subclasses of §17.2 are added with the components that raise them (PR-03).
"""

from shared.domain.enums import ErrorCode


class ReviewError(Exception):
    def __init__(self, code: ErrorCode, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class StaticAnalysisFailed(ReviewError):
    def __init__(self, safe_message: str) -> None:
        super().__init__(ErrorCode.STATIC_ANALYSIS_FAILURE, safe_message)
