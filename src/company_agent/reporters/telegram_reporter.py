"""Reporters - sends workflow results to external systems.

The TelegramReporter listens to workflow events and sends updates to Telegram.
"""

import asyncio
import logging
from typing import Optional

from telegram import Bot as TelegramBot
from telegram.error import TelegramError

from company_agent.config import TelegramConfig
from company_agent.event_bus import Event, EventBus, EventTypes


logger = logging.getLogger(__name__)


class TelegramReporter:
    """Reports workflow events to Telegram.

    Subscribes to the EventBus and sends Telegram messages for important events.
    """

    def __init__(
        self,
        config: TelegramConfig,
        event_bus: EventBus,
        bot: Optional[TelegramBot] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.bot = bot or TelegramBot(token=config.bot_token or "")

        self._subscriptions: list = []
        self._chat_messages: dict = {}  # chat_id -> message_id for streaming

    async def start(self):
        """Start listening to events and sending Telegram messages."""
        # Subscribe to relevant events
        self._subscriptions.append(
            await self.event_bus.subscribe(
                "workflow.*",
                self._handle_workflow_event,
                subscriber="telegram_reporter",
            )
        )

        self._subscriptions.append(
            await self.event_bus.subscribe(
                "task.*",
                self._handle_task_event,
                subscriber="telegram_reporter",
            )
        )

    async def stop(self):
        """Stop listening to events."""
        for sub_id in self._subscriptions:
            await self.event_bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    async def _handle_workflow_event(self, event: Event):
        """Handle workflow events."""
        chat_id = event.payload.get("chat_id")
        if not chat_id:
            return

        if event.type == EventTypes.WORKFLOW_STARTED:
            await self._send(chat_id, f"🚀 Workflow started: {event.payload.get('request', '')[:100]}")

        elif event.type == EventTypes.WORKFLOW_COMPLETED:
            status = event.payload.get("status", "unknown")
            if status == "completed":
                await self._send(chat_id, "✅ Workflow completed successfully!")
            else:
                await self._send(chat_id, f"⚠️ Workflow finished with status: {status}")

        elif event.type == EventTypes.WORKFLOW_FAILED:
            error = event.payload.get("error", "Unknown error")
            await self._send(chat_id, f"❌ Workflow failed: {error[:200]}")

        elif event.type == EventTypes.WORKFLOW_APPROVED:
            await self._send(chat_id, "✅ Workflow auto-approved!")

        elif event.type == EventTypes.WORKFLOW_REJECTED:
            decision = event.payload.get("decision", "unknown")
            await self._send(chat_id, f"⚠️ Workflow flagged for review: {decision}")

    async def _handle_task_event(self, event: Event):
        """Handle task events."""
        chat_id = event.payload.get("chat_id")
        if not chat_id:
            return

        if event.type == EventTypes.TASK_STARTED:
            task_type = event.payload.get("type", "unknown")
            await self._send(chat_id, f"🔧 Starting {task_type} task...")

        elif event.type == EventTypes.TASK_COMPLETED:
            success = event.payload.get("success")
            status = "✅" if success else "❌"
            await self._send(chat_id, f"{status} Task completed")

        elif event.type == EventTypes.TASK_FAILED:
            error = event.payload.get("error", "Unknown error")
            await self._send(chat_id, f"❌ Task failed: {error[:200]}")

        elif event.type == EventTypes.TASK_OUTPUT:
            # Streaming output - just log for now
            output = event.payload.get("output", "")
            if output:
                logger.debug(f"Task output: {output[:100]}")

    async def _send(self, chat_id: int, text: str):
        """Send a message to a Telegram chat."""
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")

    async def send_message(self, chat_id: int, text: str):
        """Send a message to a specific chat."""
        await self._send(chat_id, text)

    async def edit_message(self, chat_id: int, message_id: int, text: str):
        """Edit an existing message."""
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="Markdown",
            )
        except TelegramError as e:
            logger.error(f"Failed to edit Telegram message: {e}")
