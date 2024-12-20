"""The module for running tgcf in past mode.

- past mode can only operate with a user account.
- past mode deals with all existing messages.
"""

import asyncio
import logging
import time

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.custom.message import Message
from telethon.tl.patched import MessageService

from tgcf import config
from tgcf import storage as st
from tgcf.config import CONFIG, get_SESSION, write_config
from tgcf.plugins import apply_plugins, load_async_plugins
from tgcf.utils import clean_session_files, send_message


async def forward_job(agent_id: int) -> None:
    """Forward all existing messages in the concerned chats."""
    clean_session_files()

    # load async plugins defined in plugin_models
    await load_async_plugins()
    agent = CONFIG.login_cfg.agents[agent_id]
    if agent.user_type != 1:
        logging.warning(
            "You cannot use bot account for tgcf past mode. Telegram does not allow bots to access chat history."
        )
        return
    SESSION = get_SESSION(agent_id)
    async with TelegramClient(
        SESSION, CONFIG.login_cfg.tg.API_ID, CONFIG.login_cfg.tg.API_HASH
    ) as client:
        active_forwards = await config.load_active_forwards(
            agent_id, config.CONFIG.forwards
        )
        config.from_to = await config.load_from_to(agent_id, client, active_forwards)
        client: TelegramClient
        for from_to, forward in zip(config.from_to.items(), active_forwards):
            src, destV = from_to
            dest = destV["dest"]
            pcfg_id = destV["pcfg"]
            last_id = 0
            forward: config.Forward
            logging.info(f"Forwarding messages from {src} to {dest}")
            tm = None
            async for message in client.iter_messages(
                src, reverse=True, offset_id=forward.offset
            ):
                message: Message
                event = st.DummyEvent(message.chat_id, message.id)
                event_uid = st.EventUid(event)

                if forward.end and last_id > forward.end:
                    continue
                if isinstance(message, MessageService):
                    continue
                try:

                    tm = await apply_plugins(pcfg_id, message, tm)
                    # tm = await apply_plugins_with_tm(pcfg_id, message, tm)
                    if not tm:
                        continue
                    if not tm.get_next():
                        continue
                    st.stored[event_uid] = {}

                    if message.is_reply:
                        r_event = st.DummyEvent(
                            message.chat_id, message.reply_to_msg_id
                        )
                        r_event_uid = st.EventUid(r_event)
                    for d in dest:
                        if message.is_reply and r_event_uid in st.stored:
                            tm.reply_to = st.stored.get(r_event_uid).get(d)
                        fwded_msg = await send_message(agent_id, d, tm)
                        st.stored[event_uid].update({d: fwded_msg.id})
                    tm.clear()
                    tm = tm.get_next()
                    last_id = message.id - 1
                    logging.info(f"forwarding message with id = {last_id}")
                    forward.offset = last_id
                    write_config(CONFIG, persist=False)
                    time.sleep(CONFIG.agent_fwd_cfg[agent_id].past.delay)
                    logging.info(
                        f"slept for {CONFIG.agent_fwd_cfg[agent_id].past.delay} seconds"
                    )

                except FloodWaitError as fwe:
                    logging.info(f"Sleeping for {fwe}")
                    await asyncio.sleep(delay=fwe.seconds)
                except Exception as err:
                    logging.exception(err)
            # process the last msg
            if tm:
                st.stored[event_uid] = {}
                message = tm.message
                event = st.DummyEvent(message.chat_id, message.id)
                event_uid = st.EventUid(event)
                if message.is_reply:
                    r_event = st.DummyEvent(
                        message.chat_id, message.reply_to_msg_id
                    )
                    r_event_uid = st.EventUid(r_event)
                for d in dest:
                    if message.is_reply and r_event_uid in st.stored:
                        tm.reply_to = st.stored.get(r_event_uid).get(d)
                    fwded_msg = await send_message(agent_id, d, tm)
                    st.stored[event_uid].update({d: fwded_msg.id})
                tm.clear()
                last_id = message.id
                logging.info(f"forwarding message with id = {last_id}")
                forward.offset = last_id
                write_config(CONFIG, persist=False)
                time.sleep(CONFIG.agent_fwd_cfg[agent_id].past.delay)
                logging.info(
                    f"slept for {CONFIG.agent_fwd_cfg[agent_id].past.delay} seconds"
                )
