"""The module responsible for operating tgcf in live mode."""

import logging
import os
import sys
from typing import Union

from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.tl.custom.message import Message

from tgcf import config, const
from tgcf import storage as st
from tgcf.bot import get_events
from tgcf.config import CONFIG, get_SESSION
from tgcf.plugins import apply_plugins, load_async_plugins
from tgcf.utils import clean_session_files, send_message

current_agent: int = 0

class EventHandler:
    def __init__():
        self.tm = None
        self.ALL_EVENTS = {
    "new": (self.new_message_handler, events.NewMessage()),
    # "edited": (self.edited_message_handler, events.MessageEdited()),
    "deleted": (self.deleted_message_handler, events.MessageDeleted()),
}
    def get_all_events(self):
        return self.ALL_EVENTS

    def update_events(self, command_events):
        self.ALL_EVENTS.update(command_events)
    
    async def new_message_handler(self, event: Union[Message, events.NewMessage]) -> None:
        """Process new incoming messages."""
        chat_id = event.chat_id
    
        if chat_id not in config.from_to:
            return
        logging.info(f"New message received in {chat_id}")
        message = event.message
    
        event_uid = st.EventUid(event)
    
        length = len(st.stored)
        exceeding = length - const.KEEP_LAST_MANY
    
        if exceeding > 0:
            for key in st.stored:
                del st.stored[key]
                break
    
        dest = config.from_to.get(chat_id).get("dest")
        pcfg_id = config.from_to.get(chat_id).get("pcfg")

        try:
            self.tm = await apply_plugins(pcfg_id, message, self.tm)
            if not self.tm:
                return
            if not self.tm.get_next():
                return
        
            if event.is_reply:
                r_event = st.DummyEvent(chat_id, event.reply_to_msg_id)
                r_event_uid = st.EventUid(r_event)
        
            st.stored[event_uid] = {}
            for d in dest:
                if event.is_reply and r_event_uid in st.stored:
                    self.tm.reply_to = st.stored.get(r_event_uid).get(d)
                fwded_msg = await send_message(current_agent, d, self.tm)
                # if isinstance(fwded_msg, list):
                #     for fm in fwded_msg:
                #         st.stored[event_uid].update({d: fwded_msg})
                # else:
                #     st.stored[event_uid].update({d: fwded_msg})
            tm.clear()
            self.tm = self.tm.get_next()
        except:
            self.tm = None
    
    
    async def edited_message_handler(self, event) -> None:
        """Handle message edits."""
        message = event.message
    
        chat_id = event.chat_id
    
        if chat_id not in config.from_to:
            return
    
        logging.info(f"Message edited in {chat_id}")
    
        event_uid = st.EventUid(event)
        pcfg_id = config.from_to.get(chat_id).get("pcfg")
    
        tm = await apply_plugins(pcfg_id, message)
    
        if not tm:
            return
    
        fwded_msgs = st.stored.get(event_uid)
    
        if fwded_msgs:
            for _, msg in fwded_msgs.items():
                if (
                    config.CONFIG.agent_fwd_cfg[current_agent].live.delete_on_edit
                    == message.text
                ):
                    await msg.delete()
                    await message.delete()
                else:
                    await msg.edit(tm.text)
            return
    
        dest = config.from_to.get(chat_id).get("dest")
    
        for d in dest:
            await send_message(current_agent, d, tm)
        tm.clear()
    
    
    async def deleted_message_handler(self, event):
        """Handle message deletes."""
        chat_id = event.chat_id
        if chat_id not in config.from_to:
            return
    
        logging.info(f"Message deleted in {chat_id}")
    
        event_uid = st.EventUid(event)
        fwded_msgs = st.stored.get(event_uid)
        if fwded_msgs:
            for _, msg in fwded_msgs.items():
                await msg.delete()
            return


async def start_sync(agent_id: int) -> None:
    """Start tgcf live sync."""
    # clear past session files
    clean_session_files()
    global current_agent
    current_agent = agent_id

    eh = EventHandler()
    # load async plugins defined in plugin_models
    await load_async_plugins()

    SESSION = get_SESSION(agent_id)
    client = TelegramClient(
        SESSION,
        CONFIG.login_cfg.tg.API_ID,
        CONFIG.login_cfg.tg.API_HASH,
        sequential_updates=CONFIG.agent_fwd_cfg[agent_id].live.sequential_updates,
    )
    agent = CONFIG.login_cfg.agents[agent_id]
    if agent.user_type == 0:
        if agent.BOT_TOKEN == "":
            logging.warning("Bot token not found, but login type is set to bot.")
            sys.exit()
        await client.start(bot_token=agent.BOT_TOKEN)
    else:
        await client.start()
    config.is_bot = await client.is_bot()
    logging.info(f"config.is_bot={config.is_bot}")
    command_events = get_events()

    await config.load_admins(client)

    eh.update(command_events)

    for key, val in eh.get_all_events().items():
        if (
            config.CONFIG.agent_fwd_cfg[agent_id].live.delete_sync is False
            and key == "deleted"
        ):
            continue
        client.add_event_handler(*val)
        logging.info(f"Added event handler for {key}")

    if config.is_bot and const.REGISTER_COMMANDS:
        await client(
            functions.bots.SetBotCommandsRequest(
                scope=types.BotCommandScopeDefault(),
                lang_code="en",
                commands=[
                    types.BotCommand(command=key, description=value)
                    for key, value in const.COMMANDS.items()
                ],
            )
        )
    config.from_to = await config.load_from_to(agent_id, client, config.CONFIG.forwards)
    await client.run_until_disconnected()
