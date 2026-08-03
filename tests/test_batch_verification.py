import unittest

from bot.verification_flow import VerificationDecision, determine_verification_action


class BatchVerificationTests(unittest.TestCase):
    def test_multi_slip_batch_always_requires_manual_review(self):
        """Multi-slip batches should always require manual review, never auto-decide."""
        # Even when all conditions are met, multi-slip requires manual review
        decision = determine_verification_action(
            api_success=True,
            amount_matches=True,
            name_matches=True,
            bank_matches=True,
            multi_slip_batch=True,
        )

        self.assertEqual(decision, VerificationDecision.MANUAL_REVIEW)

    def test_single_slip_can_auto_receive_when_all_match(self):
        """Single slip can auto-receive when all conditions match."""
        decision = determine_verification_action(
            api_success=True,
            amount_matches=True,
            name_matches=True,
            bank_matches=True,
            multi_slip_batch=False,
        )

        self.assertEqual(decision, VerificationDecision.AUTO_RECEIVE)


if __name__ == "__main__":
    unittest.main()
