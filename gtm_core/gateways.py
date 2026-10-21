import re


class ApprovalGateway:
    @staticmethod
    def validate_regex_token(token: str) -> bool:
        """Validates that a token can be compiled into a valid Python regex. Raises ValueError if invalid."""
        try:
            re.compile(token)
            return True
        except re.error as e:
            raise ValueError(f"Invalid regex token '{token}': {e}") from e
