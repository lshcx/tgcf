"""The module for running tgcf in past mode.

- past mode can only operate with a user account.
- past mode deals with all existing messages.
"""

import asyncio
import logging
import time
import sys

from telethon import TelegramClient, events, functions, types
from telethon.errors.rpcerrorlist import FloodWaitError
from telethon.tl.custom.message import Message
from telethon.tl.patched import MessageService

from tgcf import config, const
from tgcf import storage as st
from tgcf.bot import get_events
from tgcf.config import CONFIG, get_SESSION, write_config
from tgcf.plugins import apply_plugins, load_async_plugins
from tgcf.utils import clean_session_files, send_message
from tgcf.live import EventHandler


class ForwardJob:

    def __init__(self):
        self.client = None
        self.ehs = {}
        clean_session_files()

    async def forward_past_one_iter(self, agent_id, src, forward, destV) -> int:
        tm = None
        last_id = 0
        dest = destV["dest"]
        pcfg_id = destV["pcfg"]
        logging.info(f"Forwarding messages from {src} to {dest}")
        count = 0
        async for message in self.client.iter_messages(
                src, reverse=True, offset_id=forward.offset
            ):
            message: Message
            event = st.DummyEvent(message.chat_id, message.id)
            event_uid = st.EventUid(event)

            if forward.end and last_id > forward.end:
                continue
            if isinstance(message, MessageService):
                continue
            count += 1
            try:

                tm = await apply_plugins(pcfg_id, message, tm)
                # tm = await apply_plugins_with_tm(pcfg_id, message, tm)
                if not tm:
                    continue
                if not tm.get_next():
                    continue
                message = tm.message
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
                    # st.stored[event_uid].update({d: fwded_msg.id})
                tm.clear()
                tm = tm.get_next()
                last_id = message.id
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
                tm = None
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
                # st.stored[event_uid].update({d: fwded_msg.id})
            tm.clear()
            last_id = message.id
            logging.info(f"forwarding message with id = {last_id}")
            forward.offset = last_id
            write_config(CONFIG, persist=False)
            time.sleep(CONFIG.agent_fwd_cfg[agent_id].past.delay)
            logging.info(
                f"slept for {CONFIG.agent_fwd_cfg[agent_id].past.delay} seconds"
            )
            count += 1
        return count
    
    async def forward_past(self, agent_id: int, from_to, forward: config.Forward) -> None:

        await load_async_plugins()
        agent = CONFIG.login_cfg.agents[agent_id]
        if agent.user_type != 1:
            logging.warning(
                "You cannot use bot account for tgcf past mode. Telegram does not allow bots to access chat history."
            )
            return
        src, destV = from_to
        retry_count = 5
        processed_count = await self.forward_past_one_iter(agent_id, src, forward, destV)
        while retry_count > 0 and processed_count > 0:
            processed_count = await self.forward_past_one_iter(agent_id, src, forward, destV)
            retry_count -= 1

    async def start_sync(self, agent_id: int) -> None:
        
        logging.getLogger("telethon").setLevel(logging.WARNING)
        
        # load async plugins defined in plugin_models
        await load_async_plugins()
        agent = CONFIG.login_cfg.agents[agent_id]
        if agent.user_type == 0:
            if agent.BOT_TOKEN == "":
                logging.warning("Bot token not found, but login type is set to bot.")
                sys.exit()
            await self.client.start(bot_token=agent.BOT_TOKEN)
        else:
            await self.client.start()
        config.is_bot = await self.client.is_bot()
        logging.info(f"config.is_bot={config.is_bot}")
        command_events = get_events()

        await config.load_admins(self.client)
        self.ehs[agent_id] = EventHandler(agent_id)
        self.ehs[agent_id].update_events(command_events)

        for key, val in self.ehs[agent_id].get_all_events().items():
            if (
                config.CONFIG.agent_fwd_cfg[agent_id].live.delete_sync is False
                and key == "deleted"
            ):
                continue
            self.client.add_event_handler(*val)
            logging.info(f"Added event handler for {key}")

        if config.is_bot and const.REGISTER_COMMANDS:
            await self.client(
                functions.bots.SetBotCommandsRequest(
                    scope=types.BotCommandScopeDefault(),
                    lang_code="en",
                    commands=[
                        types.BotCommand(command=key, description=value)
                        for key, value in const.COMMANDS.items()
                    ],
                )
            )

    async def run(self, agent_id: int):
        SESSION = get_SESSION(agent_id)
        self.client = TelegramClient(
            SESSION,
            CONFIG.login_cfg.tg.API_ID,
            CONFIG.login_cfg.tg.API_HASH,
            sequential_updates=CONFIG.agent_fwd_cfg[agent_id].live.sequential_updates,
        )
        await self.start_sync(agent_id)

        active_forwards = await config.load_active_forwards(
            agent_id, config.CONFIG.forwards
        )

        config.from_to = await config.load_from_to(agent_id, self.client, active_forwards)

        for from_to, forward in zip(config.from_to.items(), active_forwards):
            src, destV = from_to
            await self.forward_past(agent_id, from_to, forward)
            if self.ehs.get(agent_id):
                self.ehs[agent_id].update_from_to({src: destV})
        
        await self.client.run_until_disconnected()
