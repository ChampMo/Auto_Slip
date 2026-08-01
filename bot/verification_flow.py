from enum import Enum


class VerificationDecision(str, Enum):
    AUTO_RECEIVE = "auto_receive"
    AUTO_REJECT = "auto_reject"
    MANUAL_REVIEW = "manual_review"


def determine_verification_action(api_success: bool, amount_matches: bool, name_matches: bool) -> VerificationDecision:
    if not api_success:
        return VerificationDecision.MANUAL_REVIEW
    if amount_matches and name_matches:
        return VerificationDecision.AUTO_RECEIVE
    return VerificationDecision.AUTO_REJECT
