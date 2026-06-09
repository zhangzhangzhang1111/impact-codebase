import math
from dataclasses import dataclass


class BudgetExceededError(ValueError):
    pass


@dataclass(frozen=True)
class TokenBudget:
    max_input_tokens: int
    max_output_tokens: int
    reserved_output_tokens: int

    def __post_init__(self) -> None:
        if self.max_input_tokens <= 0 or self.max_output_tokens <= 0:
            raise BudgetExceededError("Token limits must be positive.")
        if self.reserved_output_tokens > self.max_input_tokens:
            raise BudgetExceededError("Reserved output tokens exceed the input context window.")

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / 4))

    def chunk_text(self, text: str) -> list[str]:
        available_tokens = self.max_input_tokens - self.reserved_output_tokens
        if available_tokens <= 0:
            raise BudgetExceededError("No input budget remains after reserving output tokens.")

        words = text.split()
        chunks: list[str] = []
        current: list[str] = []

        for word in words:
            candidate = " ".join([*current, word])
            if current and self.estimate_tokens(candidate) > available_tokens:
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)

        if current:
            chunks.append(" ".join(current))

        return chunks or [""]
