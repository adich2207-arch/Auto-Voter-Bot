"""
account_manager.py — Telethon login flow + all account actions.
"""

import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import SendReactionRequest, ImportChatInviteRequest
from telethon.tl.types import ReactionEmoji
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError,
    UserAlreadyParticipantError, PhoneNumberBannedError,
    ChannelPrivateError, UsernameInvalidError,
)

API_ID   = int(os.getenv("API_ID",   "0"))
API_HASH =     os.getenv("API_HASH", "")

# seconds of delay between each account during a campaign
SPEED_DELAY = {1: 0.5, 2: 1.5, 3: 3.0, 4: 5.0, 5: 10.0}


def _client(session_str: str = None) -> TelegramClient:
    sess = StringSession(session_str) if session_str else StringSession()
    return TelegramClient(sess, API_ID, API_HASH)


# ── Login flow ──────────────────────────────────────────────────────────────────

async def request_code(phone: str):
    """Connect and send OTP. Returns (client, phone_code_hash)."""
    c = _client()
    await c.connect()
    try:
        res = await c.send_code_request(phone)
        return c, res.phone_code_hash
    except PhoneNumberBannedError:
        await c.disconnect()
        raise


async def complete_login(client: TelegramClient, phone: str,
                         code_hash: str, code: str):
    """
    Sign in with OTP. Returns (session_string, user_info_dict).
    Raises SessionPasswordNeededError when 2FA is required.
    """
    user = await client.sign_in(phone, code, phone_code_hash=code_hash)
    session = client.session.save()
    info = {"username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", "") or ""}
    await client.disconnect()
    return session, info


async def complete_2fa(client: TelegramClient, password: str):
    """Finish 2FA login. Returns (session_string, user_info_dict)."""
    user = await client.sign_in(password=password)
    session = client.session.save()
    info = {"username": getattr(user, "username", None),
            "first_name": getattr(user, "first_name", "") or ""}
    await client.disconnect()
    return session, info


# ── Individual actions ──────────────────────────────────────────────────────────

async def _vote(c, target: str, option_index: int):
    chat, mid = target.rsplit("/", 1)
    mid = int(mid)
    entity = await c.get_entity(chat)
    msg = await c.get_messages(entity, ids=mid)
    if msg and msg.poll:
        ans = msg.poll.poll.answers[option_index]
        await c.vote_poll(entity, mid, [ans.option])


async def _react(c, target: str, emoji: str):
    chat, mid = target.rsplit("/", 1)
    entity = await c.get_entity(chat)
    await c(SendReactionRequest(
        peer=entity,
        msg_id=int(mid),
        reaction=[ReactionEmoji(emoticon=emoji)]
    ))


async def _view(c, target: str):
    chat, mid = target.rsplit("/", 1)
    entity = await c.get_entity(chat)
    await c.get_messages(entity, ids=int(mid))


async def _join(c, target: str):
    if "t.me/+" in target or "t.me/joinchat/" in target:
        invite = target.split("/")[-1].lstrip("+")
        try:
            await c(ImportChatInviteRequest(invite))
        except UserAlreadyParticipantError:
            pass
    else:
        entity = await c.get_entity(target)
        try:
            await c(JoinChannelRequest(entity))
        except UserAlreadyParticipantError:
            pass


async def _leave(c, target: str):
    entity = await c.get_entity(target)
    await c(LeaveChannelRequest(entity))


async def _refer(c, target: str, bot_username: str):
    await c.send_message(bot_username, f"/start {target}")


async def _dm(c, target: str, message: str):
    await c.send_message(target, message)


# ── Campaign runner ─────────────────────────────────────────────────────────────

async def run_campaign(accounts: list[dict], action: str, target: str,
                       extra: dict, speed: int,
                       on_progress=None) -> tuple[int, int]:
    """
    Execute `action` on every account sequentially.
    on_progress(done, total, ok, fail) is awaited after each account.
    Returns (success, fail).
    """
    delay = SPEED_DELAY.get(speed, 3.0)
    ok = fail = 0
    total = len(accounts)

    for i, acc in enumerate(accounts):
        c = _client(acc["session_str"])
        try:
            await c.connect()
            if   action in ("react", "react_view"):
                await _react(c, target, extra.get("emoji", "👍"))
                if "view" in action:
                    await _view(c, target)
            elif action in ("vote", "vote_view"):
                await _vote(c, target, int(extra.get("option_index", 0)))
                if "view" in action:
                    await _view(c, target)
            elif action == "react_vote":
                await _react(c, target, extra.get("emoji", "👍"))
                await _vote(c, target, int(extra.get("option_index", 0)))
            elif action == "react_vote_view":
                await _react(c, target, extra.get("emoji", "👍"))
                await _vote(c, target, int(extra.get("option_index", 0)))
                await _view(c, target)
            elif action == "view":
                await _view(c, target)
            elif action == "join":
                await _join(c, target)
            elif action == "leave":
                await _leave(c, target)
            elif action == "refer":
                await _refer(c, target, extra.get("bot_username", ""))
            elif action == "dm":
                await _dm(c, target, extra.get("message", ""))
            ok += 1
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 3)
            try:
                await _view(c, target)   # safest retry
                ok += 1
            except Exception:
                fail += 1
        except (ChannelPrivateError, UsernameInvalidError, Exception):
            fail += 1
        finally:
            try:
                await c.disconnect()
            except Exception:
                pass

        if on_progress:
            await on_progress(i + 1, total, ok, fail)

        if i < total - 1:
            await asyncio.sleep(delay)

    return ok, fail
