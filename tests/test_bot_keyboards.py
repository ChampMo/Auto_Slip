from bot.keyboards import get_bank_selection_keyboard


def test_bank_selection_keyboard_contains_known_mapping_values():
    keyboard = get_bank_selection_keyboard("demo-batch")
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert "SCB-CP" in labels
    assert "KB-CP" in labels
