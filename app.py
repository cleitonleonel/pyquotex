import os
import sys
import json
import time
import random
import asyncio
import logging
import argparse
import pyfiglet
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any, Callable
from functools import wraps
import locale

from pyquotex.expiration import (
    timestamp_to_date,
    get_timestamp_days_ago
)
from pyquotex.utils.processor import (
    process_candles,
    get_color,
    aggregate_candle
)
from pyquotex.config import credentials
from pyquotex.stable_api import Quotex

__author__ = "Cleiton Leonel Creton"
__version__ = "1.0.3"

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('pyquotex.log')
    ]
)
logger = logging.getLogger(__name__)

LANGUAGE_MESSAGES = {
    "pt_BR": {
        "private_version_ad": (
            "🌟✨ Esta é a versão COMUNITÁRIA da PyQuotex! ✨🌟\n"
            "🔐  Desbloqueie todo o poder e recursos extras com a nossa versão PRIVADA.\n"
            "📤  Para mais funcionalidades e suporte exclusivo, considere uma doação ao projeto.\n"
            "➡️ Contato para doações e acesso à versão privada: https://t.me/pyquotex/852"
        )
    },
    "en_US": {
        "private_version_ad": (
            "🌟✨ This is the COMMUNITY version of PyQuotex! ✨🌟\n"
            "🔐  Unlock full power and extra features with our PRIVATE version.\n"
            "📤  For more functionalities and exclusive support, please consider donating to the project.\n"
            "➡️ Contact for donations and private version access: https://t.me/pyquotex/852"
        )
    }
}


def detect_user_language() -> str:
    """Attempts to detect the user's system language."""
    try:
        system_lang = locale.getlocale()[0]
        if system_lang and system_lang.startswith("pt"):
            return "pt_BR"
        return "en_US"
    except Exception:
        return "en_US"


def ensure_connection(max_attempts: int = 5):
    """Decorator to ensure connection before executing function."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            if not self.client:
                logger.error("Quotex API client not initialized.")
                raise RuntimeError("Quotex API client not initialized.")

            if await self.client.check_connect():
                logger.debug("Already connected. Proceeding with operation.")
                return await func(self, *args, **kwargs)

            logger.info("Establishing connection...")
            check, reason = await self._connect_with_retry(max_attempts)

            if not check:
                logger.error(f"Failed to connect after multiple attempts: {reason}")
                raise ConnectionError(f"Failed to connect: {reason}")

            try:
                result = await func(self, *args, **kwargs)
                return result
            finally:
                if self.client and await self.client.check_connect():
                    await self.client.close()
                    logger.debug("Connection closed after operation.")

        return wrapper

    return decorator


class PyQuotexCLI:
    """PyQuotex CLI application for trading operations."""

    def __init__(self):
        self.client: Optional[Quotex] = None
        self.setup_client()

    def setup_client(self):
        """Initializes the Quotex API client with credentials."""
        try:
            email, password = credentials()
            self.client = Quotex(
                email=email,
                password=password,
                lang="pt"
            )
            logger.info("Quotex client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Quotex client: {e}")
            raise

    async def _connect_with_retry(self, attempts: int = 5) -> Tuple[bool, str]:
        """Internal method to attempt connection with retry logic."""
        logger.info("Attempting to connect to Quotex API...")
        check, reason = await self.client.connect()

        if not check:
            for attempt_num in range(1, attempts + 1):
                logger.warning(f"Connection failed. Attempt {attempt_num} of {attempts}.")

                session_file = Path("session.json")
                if session_file.exists():
                    session_file.unlink()
                    logger.debug("Obsolete session file removed.")

                await asyncio.sleep(2)
                check, reason = await self.client.connect()

                if check:
                    logger.info("Reconnected successfully!")
                    break

            if not check:
                logger.error(f"Failed to connect after {attempts} attempts: {reason}")
                return False, reason

        logger.info(f"Connected successfully: {reason}")
        return check, reason

    def display_banner(self):
        """Displays the application banner, including the private version ad."""
        custom_font = pyfiglet.Figlet(font="ansi_shadow")
        ascii_art = custom_font.renderText("PyQuotex")

        user_lang = detect_user_language()
        ad_message = LANGUAGE_MESSAGES.get(user_lang, LANGUAGE_MESSAGES["en_US"])["private_version_ad"]

        banner = f"""{ascii_art}
        Author: {__author__} | Version: {__version__}
        Use with moderation, because management is everything!
        Support: cleiton.leonel@gmail.com or +55 (27) 9 9577-2291

        {ad_message}

        """
        print(banner)

    @ensure_connection()
    async def test_connection(self) -> None:
        """Tests the connection to the Quotex API."""
        logger.info("Running connection test.")
        is_connected = await self.client.check_connect()

        if is_connected:
            logger.info("Connection test successful.")
            print("✅ Connection successful!")
        else:
            logger.error("Connection test failed.")
            print("❌ Connection failed!")

    @ensure_connection()
    async def get_balance(self) -> None:
        """Gets the current account balance (practice by default)."""
        logger.info("Getting account balance.")
        await self.client.change_account("PRACTICE")
        balance = await self.client.get_balance()
        logger.info(f"Current balance: {balance}")
        print(f"💰 Current Balance: R$ {balance:.2f}")

    @ensure_connection()
    async def get_profile(self) -> None:
        """Gets user profile information."""
        logger.info("Getting user profile.")

        profile = await self.client.get_profile()

        description = (
            f"\n👤 User Profile:\n"
            f"Name: {profile.nick_name}\n"
            f"Demo Balance: R$ {profile.demo_balance:.2f}\n"
            f"Live Balance: R$ {profile.live_balance:.2f}\n"
            f"ID: {profile.profile_id}\n"
            f"Avatar: {profile.avatar}\n"
            f"Country: {profile.country_name}\n"
            f"Time Zone: {profile.offset}\n"
        )
        logger.info("Profile retrieved successfully.")
        print(description)

    @ensure_connection()
    async def buy_simple(self, amount: float = 50, asset: str = "EURUSD_otc",
                         direction: str = "call", duration: int = 60) -> None:
        """Executes a simple buy operation."""
        logger.info(f"Executing simple buy: {amount} on {asset} in {direction} direction for {duration}s.")

        await self.client.change_account("PRACTICE")
        asset_name, asset_data = await self.client.get_available_asset(asset, force_open=True)

        if not asset_data or len(asset_data) < 3 or not asset_data[2]:
            logger.error(f"Asset {asset} is closed or invalid.")
            print(f"❌ ERROR: Asset {asset} is closed or invalid.")
            return

        logger.info(f"Asset {asset} is open.")
        status, buy_info = await self.client.buy(
            amount, asset_name, direction, duration, time_mode="TIMER"
        )

        if status:
            logger.info(f"Buy successful: {buy_info}")
            print(f"✅ Buy executed successfully!")
            print(f"Amount: R$ {amount:.2f}")
            print(f"Asset: {asset}")
            print(f"Direction: {direction.upper()}")
            print(f"Duration: {duration}s")
            print(f"Order ID: {buy_info.get('id', 'N/A')}")
        else:
            logger.error(f"Buy failed: {buy_info}")
            print(f"❌ Buy failed: {buy_info}")

        balance = await self.client.get_balance()
        logger.info(f"Current balance: {balance}")
        print(f"💰 Current Balance: R$ {balance:.2f}")

    @ensure_connection()
    async def buy_and_check_win(self, amount: float = 50, asset: str = "EURUSD_otc",
                                direction: str = "put", duration: int = 60) -> None:
        """Executes a buy operation and checks if it was a win or loss."""
        logger.info(
            f"Executing buy and checking result: {amount} on {asset} in {direction} direction for {duration}s.")

        await self.client.change_account("PRACTICE")
        balance_before = await self.client.get_balance()
        logger.info(f"Balance before trade: {balance_before}")
        print(f"💰 Balance Before: R$ {balance_before:.2f}")

        asset_name, asset_data = await self.client.get_available_asset(asset, force_open=True)

        if not asset_data or len(asset_data) < 3 or not asset_data[2]:
            logger.error(f"Asset {asset} is closed or invalid.")
            print(f"❌ ERROR: Asset {asset} is closed or invalid.")
            return

        logger.info(f"Asset {asset} is open.")
        status, buy_info = await self.client.buy(amount, asset_name, direction, duration,
                                                 time_mode="TIMER")

        if not status:
            logger.error(f"Buy operation failed: {buy_info}")
            print(f"❌ Buy operation failed! Details: {buy_info}")
            return

        print(f"📊 Trade executed (ID: {buy_info.get('id', 'N/A')}), waiting for result...")
        logger.info(f"Waiting for trade result ID: {buy_info.get('id', 'N/A')}...")

        if await self.client.check_win(buy_info["id"]):
            profit = self.client.get_profit()
            logger.info(f"WIN! Profit: {profit}")
            print(f"🎉 WIN! Profit: R$ {profit:.2f}")
        else:
            loss = self.client.get_profit()
            logger.info(f"LOSS! Loss: {loss}")
            print(f"💔 LOSS! Loss: R$ {loss:.2f}")

        balance_after = await self.client.get_balance()
        logger.info(f"Balance after trade: {balance_after}")
        print(f"💰 Current Balance: R$ {balance_after:.2f}")

    @ensure_connection()
    async def get_candles(self, asset: str = "CHFJPY_otc", period: int = 60,
                          offset: int = 3600) -> None:
        """Gets historical candle data (candlesticks)."""
        logger.info(f"Getting candles for {asset} with period of {period}s.")

        end_from_time = time.time()
        candles = await self.client.get_candles(asset, end_from_time, offset, period)

        if not candles:
            logger.warning("No candles found for the specified asset.")
            print("⚠️ No candles found for the specified asset.")
            return

        if not candles[0].get("open"):
            candles = process_candles(candles, period)

        candles_color = []
        if len(candles) > 0:
            candles_color = [get_color(candle) for candle in candles if 'open' in candle and 'close' in candle]
        else:
            logger.warning("Not enough candle data to determine colors.")

        logger.info(f"Retrieved {len(candles)} candles.")

        print(f"\n📈 Candles (Candlesticks) for {asset} (Period: {period}s):")
        print(f"Total candles: {len(candles)}")
        if candles_color:
            print(f"Colors of last 10 candles: {' '.join(candles_color[-10:])}")
        else:
            print("   Candle colors not available.")

        print("\n   Last 5 candles:")
        for i, candle in enumerate(candles[-5:]):
            color = candles_color[-(5 - i)] if candles_color and (5 - i) <= len(candles_color) else "N/A"
            emoji = "🟢" if color == "green" else ("🔴" if color == "red" else "⚪")
            print(
                f"{emoji} Open: {candle.get('open', 'N/A'):.4f} → Close: {candle.get('close', 'N/A'):.4f} (Time: {time.strftime('%H:%M:%S', time.localtime(candle.get('time', 0)))})")

    @ensure_connection()
    async def get_assets_status(self) -> None:
        """Gets the status of all available assets (open/closed)."""
        logger.info("Getting assets status.")

        print("\n📊 Assets Status:")
        open_count = 0
        closed_count = 0

        all_assets = self.client.get_all_asset_name()
        if not all_assets:
            logger.warning("Could not retrieve assets list.")
            print("⚠️ Could not retrieve assets list.")
            return

        for asset_info in all_assets:
            asset_symbol = asset_info[0]
            asset_display_name = asset_info[1]

            _, asset_open_data = await self.client.check_asset_open(asset_symbol)

            is_open = False
            if asset_open_data and len(asset_open_data) > 2:
                is_open = asset_open_data[2]

            status_text = "OPEN" if is_open else "CLOSED"
            emoji = "🟢" if is_open else "🔴"

            print(f"{emoji} {asset_display_name} ({asset_symbol}): {status_text}")

            if is_open:
                open_count += 1
            else:
                closed_count += 1

            logger.debug(f"Asset {asset_symbol}: {status_text}")

        print(f"\n📈 Summary: {open_count} open assets, {closed_count} closed assets.")

    @ensure_connection()
    async def get_payment_info(self) -> None:
        """Gets payment information (payout) for all assets."""
        logger.info("Getting payment information.")

        all_data = self.client.get_payment()
        if not all_data:
            logger.warning("No payment information found.")
            print("⚠️ No payment information found.")
            return

        print("\n💰 Payment Information (Payout):")
        print("-" * 50)

        for asset_name, asset_data in list(all_data.items())[:10]:
            profit_1m = asset_data.get("profit", {}).get("1M", "N/A")
            profit_5m = asset_data.get("profit", {}).get("5M", "N/A")
            is_open = asset_data.get("open", False)

            status_text = "OPEN" if is_open else "CLOSED"
            emoji = "🟢" if is_open else "🔴"

            print(f"{emoji} {asset_name} - {status_text}")
            print(f"1M Profit: {profit_1m}% | 5M Profit: {profit_5m}%")
            print("-" * 50)

    @ensure_connection()
    async def balance_refill(self, amount: float = 5000) -> None:
        """Refills the practice account balance."""
        logger.info(f"Refilling practice account balance with R$ {amount:.2f}.")

        await self.client.change_account("PRACTICE")
        result = await self.client.edit_practice_balance(amount)

        if result:
            logger.info(f"Balance refill successful: {result}")
            print(f"✅ Practice account balance refilled to R$ {amount:.2f} successfully!")
        else:
            logger.error("Balance refill failed.")
            print("❌ Practice account balance refill failed.")

        new_balance = await self.client.get_balance()
        print(f"💰 New Balance: R$ {new_balance:.2f}")

    @ensure_connection()
    async def get_realtime_price(self, asset: str = "EURJPY_otc") -> None:
        """Monitors the real-time price of an asset."""
        logger.info(f"Getting real-time price for {asset}.")

        asset_name, asset_data = await self.client.get_available_asset(asset, force_open=True)

        if not asset_data or len(asset_data) < 3 or not asset_data[2]:
            logger.error(f"Asset {asset} is closed or invalid for real-time monitoring.")
            print(f"❌ ERROR: Asset {asset} is closed or invalid for monitoring.")
            return

        logger.info(f"Asset {asset} is open. Starting real-time price monitoring.")
        await self.client.start_realtime_price(asset, 60)

        print(f"\n📊 Monitoring real-time price for {asset}")
        print("Press Ctrl+C to stop monitoring...")
        print("-" * 60)

        try:
            while True:
                candle_price_data = await self.client.get_realtime_price(asset_name)
                if candle_price_data:
                    latest_data = candle_price_data[-1]
                    timestamp = latest_data['time']
                    price = latest_data['price']
                    formatted_time = time.strftime('%H:%M:%S', time.localtime(timestamp))

                    print(f"📈 {asset} | {formatted_time} | Price: {price:.5f}", end="\r")
                await asyncio.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Real-time price monitoring interrupted by user.")
            print("\n✅ Real-time monitoring stopped.")
        finally:
            await self.client.stop_realtime_price(asset_name)
            logger.info(f"Real-time price subscription for {asset_name} stopped.")

    @ensure_connection()
    async def get_signal_data(self) -> None:
        """Gets and monitors trading signal data."""
        logger.info("Getting trading signal data.")

        self.client.start_signals_data()
        print("\n📡 Monitoring trading signals...")
        print("Press Ctrl+C to stop monitoring...")
        print("-" * 60)

        try:
            while True:
                signals = self.client.get_signal_data()
                if signals:
                    print(f"🔔 New Signal Received:")
                    print(json.dumps(signals, indent=2,
                                     ensure_ascii=False))
                    print("-" * 60)
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Signal monitoring interrupted by user.")
            print("\n✅ Signal monitoring stopped.")
        finally:
            pass


def create_parser() -> argparse.ArgumentParser:
    """Creates and configures the command line argument parser."""
    parser = argparse.ArgumentParser(
        description="PyQuotex CLI - Trading automation tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  python app.py test-connection
  python app.py get-balance
  python app.py buy-simple --amount 100 --asset EURUSD_otc --direction call
  python app.py get-candles --asset GBPUSD --period 300
  python app.py realtime-price --asset EURJPY_otc
  python app.py signals
        """
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"PyQuotex {__version__}"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable detailed logging mode (DEBUG)."
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress most output except errors."
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("test-connection", help="Test connection to Quotex API.")

    subparsers.add_parser("get-balance", help="Get current account balance (practice by default).")

    subparsers.add_parser("get-profile", help="Get user profile information.")

    buy_parser = subparsers.add_parser("buy-simple", help="Execute a simple buy operation.")
    buy_parser.add_argument("--amount", type=float, default=50, help="Amount to invest.")
    buy_parser.add_argument("--asset", default="EURUSD_otc", help="Asset to trade.")
    buy_parser.add_argument("--direction", choices=["call", "put"], default="call",
                            help="Trade direction (call for up, put for down).")
    buy_parser.add_argument("--duration", type=int, default=60, help="Duration in seconds.")

    buy_check_parser = subparsers.add_parser("buy-and-check", help="Execute a buy and check win/loss.")
    buy_check_parser.add_argument("--amount", type=float, default=50, help="Amount to invest.")
    buy_check_parser.add_argument("--asset", default="EURUSD_otc", help="Asset to trade.")
    buy_check_parser.add_argument("--direction", choices=["call", "put"], default="put",
                                  help="Trade direction.")
    buy_check_parser.add_argument("--duration", type=int, default=60, help="Duration in seconds.")

    candles_parser = subparsers.add_parser("get-candles", help="Get historical candle data (candlesticks).")
    candles_parser.add_argument("--asset", default="CHFJPY_otc", help="Asset to get candles for.")
    candles_parser.add_argument("--period", type=int, default=60,
                                help="Candle period in seconds (e.g., 60 for 1 minute).")
    candles_parser.add_argument("--offset", type=int, default=3600, help="Offset in seconds to fetch candles.")

    subparsers.add_parser("assets-status", help="Get status (open/closed) of all available assets.")

    subparsers.add_parser("payment-info", help="Get payment information (payout) for all assets.")

    refill_parser = subparsers.add_parser("balance-refill", help="Refill practice account balance.")
    refill_parser.add_argument("--amount", type=float, default=5000, help="Amount to refill practice account.")

    price_parser = subparsers.add_parser("realtime-price", help="Monitor real-time price of an asset.")
    price_parser.add_argument("--asset", default="EURJPY_otc", help="Asset to monitor.")

    subparsers.add_parser("signals", help="Monitor trading signal data.")

    return parser


async def main():
    """Main entry point of the CLI application."""
    parser = create_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    else:
        logging.getLogger().setLevel(logging.INFO)

    cli = PyQuotexCLI()

    if not args.quiet:
        cli.display_banner()
        await asyncio.sleep(1)

    try:
        if args.command == "test-connection":
            await cli.test_connection()
        elif args.command == "get-balance":
            await cli.get_balance()
        elif args.command == "get-profile":
            await cli.get_profile()
        elif args.command == "buy-simple":
            await cli.buy_simple(args.amount, args.asset, args.direction, args.duration)
        elif args.command == "buy-and-check":
            await cli.buy_and_check_win(args.amount, args.asset, args.direction, args.duration)
        elif args.command == "get-candles":
            await cli.get_candles(args.asset, args.period, args.offset)
        elif args.command == "assets-status":
            await cli.get_assets_status()
        elif args.command == "payment-info":
            await cli.get_payment_info()
        elif args.command == "balance-refill":
            await cli.balance_refill(args.amount)
        elif args.command == "realtime-price":
            await cli.get_realtime_price(args.asset)
        elif args.command == "signals":
            await cli.get_signal_data()
        else:
            parser.print_help()

    except KeyboardInterrupt:
        logger.info("CLI operation interrupted by user.")
        print("\n✅ Operation interrupted by user.")
    except ConnectionError as e:
        logger.error(f"Connection error during command execution: {e}")
        print(f"❌ Connection error: {e}")
    except RuntimeError as e:
        logger.error(f"Runtime error: {e}")
        print(f"❌ Error: {e}")
    except Exception as e:
        logger.critical(f"Unexpected error occurred during command execution: {e}", exc_info=True)
        print(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ Program terminated by user.")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Fatal error in main execution: {e}", exc_info=True)
        print(f"❌ FATAL ERROR: {e}")
        sys.exit(1)
