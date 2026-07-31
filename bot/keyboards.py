from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from services.easyslip import ACCOUNT_MAPPING


def get_approval_keyboard(batch_id: str):
    # ใช้ batch_id เป็นตัวอ้างอิงแทน
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id

    keyboard = [
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{short_ref}"),
            InlineKeyboardButton("✅ Receive", callback_data=f"receive_{short_ref}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_bank_selection_keyboard(batch_id: str):
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id
    bank_values = sorted(set(ACCOUNT_MAPPING.values()))

    buttons = [
        InlineKeyboardButton(value, callback_data=f"bank_{short_ref}_{value}")
        for value in bank_values
    ]

    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data=f"back_{short_ref}")
    ])
    return InlineKeyboardMarkup(keyboard)