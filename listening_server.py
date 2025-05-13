import sys
import os
import logging
import argparse
from typing import Optional, Tuple
import json
import socket
import sqlite3
import time
import threading
import base64
from bs4 import BeautifulSoup, NavigableString
from smartcard.System import readers
from smartcard.util import toHexString
from smartcard.Exceptions import NoCardException, SmartcardException, CardConnectionException
import serial
import serial.tools.list_ports
from adafruit_pn532.uart import PN532_UART

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class YuGiOhCard:
    def __init__(self, identifier, passcode, konami_id, variant, set_id, lang, number, rarity, edition):
        self.identifier = identifier
        self.passcode = passcode
        self.konami_id = konami_id
        self.variant = variant
        self.set_id = set_id
        self.lang = lang
        self.number = number
        self.rarity = rarity
        self.edition = edition  # New field for edition

        # Validate and encode the card during initialization
        self.encoded_data = self.encode_card()

    def encode_card(self):
        if len(self.identifier) != 4 or not self.identifier.startswith("YG"):
            raise ValueError("Identifier must be 4 characters long and start with 'YG'.")
        if len(self.passcode) < 5:
            raise ValueError("Passcode must be 5 or more digits.")
        self.passcode = self.passcode.ljust(10, '-')
        if not str(self.konami_id).isdigit() or len(str(self.konami_id)) > 8:
            raise ValueError("Konami DB ID must be a numeric value and no longer than 8 characters.")
        self.konami_id = str(self.konami_id).ljust(8, '-')
        if not self.variant.isdigit() or len(self.variant) != 4:
            raise ValueError("Variant must be a 4-digit number.")
        self.variant = self.variant.zfill(4)
        if len(self.set_id) <= 2:
            raise ValueError("Set ID must be exactly 3-4 characters long.")
        self.set_id = self.set_id.ljust(4, '-')
        if len(self.lang) != 2:
            raise ValueError("Language must be exactly 2 characters.")
        if len(self.number) != 3:
            raise ValueError("Card number must be exactly 3 digits long.")
        self.number = str(self.number).zfill(3)
        if len(self.rarity) > 2:
            raise ValueError("Rarity must be a maximum of 2 characters.")
        self.rarity = self.rarity.ljust(2, '-')
        if len(self.edition) > 2:
            raise ValueError("Edition must be a maximum of 2 characters.")
        self.edition = self.edition.ljust(2, '-')
        return f"{self.identifier}{self.passcode}{self.konami_id}{self.variant}{self.set_id}{self.lang}{self.number}{self.rarity}{self.edition}XXX"

    @classmethod
    def decode_card(cls, data):
        if len(data) < 41:
            raise ValueError("Encoded data must be more than 42 bytes long.")
        identifier = data[:4]
        passcode = data[4:14].rstrip('-')
        konami_id = data[14:22].rstrip('-')
        variant = data[22:26]
        set_id = data[26:30]
        lang = data[30:32]
        number = data[32:35]
        rarity = data[35:37].strip()
        edition = data[37:39].strip()
        if not identifier.startswith("YG"):
            raise ValueError("Invalid identifier. It must start with 'YG'.")
        if len(passcode) < 5:
            raise ValueError("Invalid passcode. It should be 5 or more digits.")
        if not konami_id.isdigit() or len(konami_id) > 8:
            raise ValueError("Invalid Konami DB ID. It should be numeric and at most 8 digits long.")
        if not variant.isdigit() or len(variant) != 4:
            raise ValueError("Invalid variant. It should be a 4-digit number.")
        if len(set_id) <= 2:
            raise ValueError("Invalid Set ID. It should be exactly 4 characters.")
        if len(lang) != 2:
            raise ValueError("Invalid language. It should be exactly 2 characters.")
        if len(number) != 3:
            raise ValueError("Invalid card number. It should be a 3-digit number.")
        if len(rarity) > 4:
            raise ValueError("Invalid rarity. It should be no more than 4 characters.")
        if len(edition) > 2:
            raise ValueError("Invalid edition. It should be no more than 2 characters.")
        return {
            "identifier": identifier,
            "passcode": passcode,
            "konami_id": konami_id,
            "variant": variant,
            "set_id": set_id,
            "lang": lang,
            "number": number,
            "rarity": rarity,
            "edition": edition
        }

    def get_encoded_data(self):
        return self.encoded_data

    def __repr__(self):
        return (f"YuGiOhCard(identifier={self.identifier}, passcode={self.passcode}, "
                f"konami_id={self.konami_id}, variant={self.variant}, set_id={self.set_id}, "
                f"lang={self.lang}, number={self.number}, rarity={self.rarity}, edition={self.edition})")


class SQLiteReader:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def fetch_card_data(self, card_id: str) -> Optional[Tuple[str, bytes]]:
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT json_data, image_cropped FROM cards WHERE card_id = ?", (card_id,))
            result = cursor.fetchone()
            conn.close()
            return result
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return None


class CinematicLogoPrinter:
    color_map = {
        "black": "\033[30m",
        "#000": "\033[30m",
        "#000000": "\033[30m",
        "grey": "\033[90m",
        "gray": "\033[90m",
        "#808080": "\033[90m",
        "white": "\033[97m",
        "#fff": "\033[97m",
        "#ffffff": "\033[97m",
    }

    def __init__(self, html_file_path):
        self.html_file_path = html_file_path
        self.logo_lines = self._load_logo_from_html(html_file_path)

    def _get_ansi_color(self, style):
        if not style:
            return ""
        for declaration in style.split(";"):
            if ":" in declaration:
                prop, val = declaration.split(":", 1)
                if prop.strip().lower() == "color":
                    color_val = val.strip().lower()
                    return self.color_map.get(color_val, "")
        return ""

    def _process_node(self, node, inherited_color=""):
        if isinstance(node, NavigableString):
            return inherited_color + str(node) + "\033[0m"
        else:
            style = node.get("style", "")
            color_code = self._get_ansi_color(style)
            if not color_code:
                color_code = inherited_color
            output = ""
            for child in node.children:
                output += self._process_node(child, color_code)
            return output

    def _load_logo_from_html(self, file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        soup = BeautifulSoup(html_content, 'html.parser')
        pre_tag = soup.find('pre')
        if pre_tag:
            logo_text = self._process_node(pre_tag)
        else:
            logo_text = self._process_node(soup)
        return logo_text.splitlines()

    def clear_console(self):
        print("\033[2J\033[H", end="")

    def print_line(self, text, char_delay=0, sleep=0):
        """Prints a line character by character with a small delay."""
        for char in text:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(char_delay)
        print()  # Move to next line after printing the full line
        if not sleep == 0:
            time.sleep(sleep)

    def display_logo(self, delay=0.1, char_delay=0):
        """Prints the logo with a delay between lines and per character."""
        self.clear_console()
        for line in self.logo_lines[1::]:
            self.print_line(line, char_delay=char_delay)
            time.sleep(delay)  # Optional delay between lines


class NFCReader:
    """A class to read NFC tags using either PN532 or pyscard interfaces."""

    TAG_PATTERN = "YG"
    OTHER_APP_PORT = 41112
    OTHER_APP_HOST = 'localhost'
    EXTERNAL_PORT = 41114
    GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]

    def __init__(self, db_path: str, host: str = None, port: int = None, debug: bool = False, external_listen_port: int = None):
        """Initialize NFCReader with database path and optional connection settings."""
        self.db_path = db_path
        self.base_db_path = self._get_base_db_path(db_path)
        self.running = False
        self.threads = []
        self.debug = debug
        self.host = host or self.OTHER_APP_HOST
        self.port = port or self.OTHER_APP_PORT
        # New attribute for the external listener port
        self.external_listen_port = external_listen_port or self.EXTERNAL_PORT

        # Reader state
        self.reader = None
        self.pn532 = None
        self.uart = None

        # Locks and state tracking
        self.uid_lock = threading.Lock()
        self.current_tag_uid = None
        self.active_interface = None
        self.interface_status = {"pn532": False, "pyscard": False}

        # Logging state flags
        self.no_reader_logged = False
        self.connection_error_logged = False
        self.scanning_for_pn532_printed = False
        self.no_pn532_msg_printed = False

    @staticmethod
    def _get_base_db_path(db_path):
        """Get the base directory of the database path."""
        return os.path.dirname(os.path.abspath(db_path))

    def start(self):
        """Start the NFC reader threads if not already running."""
        if self.running:
            logger.info("Listener already running.")
            return

        self.running = True
        self._start_listener_threads()

        logger.info("Started NFC Listening Threads.")
        logger.info(f"Loaded DB Path: {self.db_path}")
        logger.info(f"Transmitting Card Data on {self.host}:{self.port}")
        logger.info(f"Listening for external commands on port {self.external_listen_port}")

    def stop(self):
        """Stop all NFC reader threads."""
        self.running = False
        for thread in self.threads:
            thread.join()
        logger.info("Stopped all NFC listening threads.")

    def _start_listener_threads(self):
        """Start the PN532, pyscard, and external command listener threads."""
        pn532_thread = threading.Thread(target=self._listen_pn532, daemon=True)
        self.threads.append(pn532_thread)
        pn532_thread.start()

        pyscard_thread = threading.Thread(target=self._listen_pyscard, daemon=True)
        self.threads.append(pyscard_thread)
        pyscard_thread.start()

        external_thread = threading.Thread(target=self._listen_for_external_command, daemon=True)
        self.threads.append(external_thread)
        external_thread.start()

    def _init_pn532(self):
        """Initialize the PN532 NFC reader if available."""
        if not self.scanning_for_pn532_printed:
            print("Scanning for PN532...")
            self.scanning_for_pn532_printed = True

        for port in serial.tools.list_ports.comports():
            try:
                print(f"Trying {port.device} for PN532...")
                uart = serial.Serial(port.device, baudrate=115200, timeout=1)
                pn532 = PN532_UART(uart, debug=self.debug)

                firmware = pn532.firmware_version
                if firmware:
                    print(f"PN532 detected on {port.device} (Firmware: {firmware[1]}.{firmware[2]})")
                    self.uart = uart
                    self.pn532 = pn532
                    self.pn532.SAM_configuration()
                    print("PN532 initialized. Waiting for an NFC card...")

                    self.scanning_for_pn532_printed = False
                    self.no_pn532_msg_printed = False
                    return True
            except Exception:
                continue

        if not self.no_pn532_msg_printed:
            print(f"No PN532 detected. Retrying in 1 second...")
            self.no_pn532_msg_printed = True
        time.sleep(1)
        return False

    def _check_reader_connection(self):
        """Check for available pyscard readers."""
        available_readers = readers()
        if available_readers:
            self.reader = available_readers[0]
            logger.info(f"pyscard: New NFC reader connected: {self.reader}")
            self.no_reader_logged = False
            self.connection_error_logged = False
            return True
        else:
            if not self.no_reader_logged:
                logger.info("pyscard: No NFC reader detected. Waiting for device...")
                self.no_reader_logged = True
            time.sleep(1)
            return False

    def _listen_pn532(self):
        """Thread function to listen for NFC tags using PN532."""
        while self.running:
            if self.pn532 is None:
                if not self._init_pn532():
                    continue

            try:
                uid = self.pn532.read_passive_target(timeout=0.5)
            except Exception as e:
                logger.error(f"PN532 read error: {e}")
                logger.info("PN532 disconnected. Attempting reinitialization...")
                self.pn532 = None
                time.sleep(1)
                continue

            with self.uid_lock:
                if uid is not None:
                    self.interface_status["pn532"] = True
                    if self.current_tag_uid is None:
                        self.current_tag_uid = uid
                        self.active_interface = "pn532"
                        logger.info(f"PN532 detected card. UID: {[hex(x) for x in uid]}")
                        full_data = self._read_ntag213_pages()
                        self._process_tag_data(full_data)
                else:
                    self.interface_status["pn532"] = False

            self._check_card_removal()
            time.sleep(0.1)

    def _listen_pyscard(self):
        """Thread function to listen for NFC tags using pyscard."""
        while self.running:
            if self.reader is None:
                self._check_reader_connection()
                continue

            try:
                connection = self.reader.createConnection()
                connection.connect()
                response, sw1, sw2 = connection.transmit(self.GET_UID)
                success = (sw1 == 0x90 and sw2 == 0x00)
            except Exception:
                response = None
                success = False

            with self.uid_lock:
                if response is not None and success:
                    uid = toHexString(response)
                    self.interface_status["pyscard"] = True
                    if self.current_tag_uid is None:
                        self.current_tag_uid = uid
                        self.active_interface = "pyscard"
                        logger.info(f"pyscard detected card. UID: {uid}")
                        tag_page = self._read_page(connection, 4)
                        if tag_page:
                            full_data = self._read_full_tag_data(connection)
                            self._process_tag_data(bytes(full_data))
                else:
                    self.interface_status["pyscard"] = False

            self._check_card_removal()
            time.sleep(0.1)

    def _listen_for_external_command(self):
        """
        Thread function to listen for a string command from another Python app.
        When a string is received, it is sent to _process_tag_data.
        """
        server_address = ('', self.external_listen_port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(server_address)
            s.listen(5)
            s.settimeout(1.0)
            logger.info(f"External command listener started on port {self.external_listen_port}")

            while self.running:
                try:
                    client_socket, addr = s.accept()
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.error(f"Listener error: {e}")
                    continue

                with client_socket:
                    try:
                        data = client_socket.recv(1024)
                        if data:
                            logger.info(f"Received external command: {data.decode('utf-8', errors='ignore').strip()}")
                            # Pass the received data to the processing method
                            if data.startswith(b"RemovedTag"):
                                self._send_to_other_app(b'{"status":"CardRemoved"}')
                            else:
                                self._process_tag_data(data)
                    except Exception as e:
                        logger.error(f"Error processing external command: {e}")

    def _read_ntag213_pages(self, start_page=4, end_page=14):
        """Read multiple pages from NTAG213 using PN532."""
        print(f"PN532: Reading NTAG213 memory pages from {start_page} to {end_page}:")
        pages = []

        for page in range(start_page, end_page + 1):
            try:
                data = self.pn532.ntag2xx_read_block(page)
                if data:
                    pages.append(data)
            except Exception as e:
                logger.error(f"PN532: Error reading page {page}: {e}")

        raw_data = b"".join(pages)

        try:
            decoded_string = raw_data.decode("utf-8", errors="ignore")
            logger.info(f"PN532: Decoded String: {decoded_string.strip()}")
        except Exception as e:
            logger.error(f"PN532: Error decoding raw data: {e}")

        return raw_data

    def _read_page(self, connection, page):
        """Read a single page from NFC tag using pyscard."""
        read_command = [0xFF, 0xB0, 0x00, page, 0x04]
        response, sw1, sw2 = connection.transmit(read_command)

        if sw1 == 0x90 and sw2 == 0x00:
            return response

        logger.error(f"pyscard: Read failed on page {page}. SW1: {sw1}, SW2: {sw2}")
        return None

    def _read_full_tag_data(self, connection):
        """Read all relevant pages from NFC tag using pyscard."""
        full_data = []
        for page in range(4, 15):
            page_data = self._read_page(connection, page)
            if page_data:
                full_data.extend(page_data)
        return full_data

    def _check_card_removal(self):
        """Check if card has been removed from both interfaces."""
        with self.uid_lock:
            if self.current_tag_uid is not None:
                if not (self.interface_status["pn532"] or self.interface_status["pyscard"]):
                    logger.info(f"Card removed! UID: {self.current_tag_uid}")
                    self._send_to_other_app(b'{"status":"CardRemoved"}')
                    self.current_tag_uid = None
                    self.active_interface = None

    def _process_tag_data(self, data):
        """Process the data read from an NFC tag."""
        logger.info(f"Processing tag data: {data[:-2]}")

        try:
            trimmed = data[:-2]
            decoded_card = YuGiOhCard.decode_card(trimmed.decode("utf-8"))
        except ValueError:
            logger.error("Error: ValueError on Decode.")
            return

        read_passcode = str(decoded_card.get("passcode"))
        db_reader = SQLiteReader(self.db_path)
        result = db_reader.fetch_card_data(read_passcode)

        if not result:
            print("Card not found.")
            return

        card_data_json, image_path = result
        card_image = self._load_card_image(image_path)
        card_metadata = self._extract_card_metadata(decoded_card)

        final_dict = {
            "status": "NewCard",
            "card_data": card_data_json,
            "passcode": read_passcode,
            "edition": card_metadata["edition_str"],
            "set_string": card_metadata["set_str"],
            "card_image": card_image
        }

        data_final = json.dumps(final_dict).encode("utf-8")
        self._send_to_other_app(data_final)
        self._print_ascii_box(read_passcode, trimmed.decode("utf-8"), read_passcode)

    def _load_card_image(self, image_path):
        """Load and encode card image from file."""
        full_path = os.path.join(self.base_db_path, image_path)

        if not os.path.exists(full_path):
            if getattr(sys, 'frozen', False):
                base_path = sys._MEIPASS
            else:
                base_path = os.getcwd()
            full_path = os.path.join(base_path, "unknowncardart.png")

        with open(full_path, "rb") as f:
            image_bytes = f.read()
            return base64.b64encode(image_bytes).decode('utf-8')

    def _extract_card_metadata(self, decoded_card):
        """Extract metadata from decoded card data."""
        set_id = decoded_card.get("set_id", "")
        lang = decoded_card.get("lang", "")
        number = str(decoded_card.get("number", ""))
        set_str = f"{set_id}-{lang}{number}"

        edition = decoded_card.get("edition", "")
        edition_str = ""
        if edition:
            edition_json_path = os.path.join(self.base_db_path, "edition.json")
            with open(edition_json_path, "r") as edition_json_file:
                edition_json = json.load(edition_json_file)
            matched_key = next((key for key, value in edition_json.items() if value == edition), None)
            if matched_key:
                edition_str = matched_key

        return {
            "set_str": set_str,
            "edition_str": edition_str
        }

    def _print_ascii_box(self, uid, raw_data, card_name):
        """Print information about the card in an ASCII box."""
        border = '+' + '-' * 60 + '+'
        print(border)
        print(f" Raw Data: {raw_data} ")
        print(f" Card ID: {card_name} ")
        print(border)

    def _send_to_other_app(self, data):
        """Send data to another application via socket."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.host, self.port))
                s.sendall(data)
                logger.info("Sent data to Card Viewer App.")
        except Exception as e:
            logger.error(f"Error sending data: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="YuGiOh Card Loader Server")
    parser.add_argument("--db", type=str, help="Path to the SQLite DB file")
    parser.add_argument("--skip-banner", action="store_true", help="Skip printing the banner and delayed text")
    parser.add_argument("--port", type=int, help="Port for NFC Reader (default: 41112)")
    parser.add_argument("--address", type=str, help="Address for NFC Reader (default: localhost)")

    args = parser.parse_args()

    # Check if --db is provided, otherwise use cards.db if it exists
    if not args.db:
        DEFAULT_DB = "cards.db"
        cwd_db_path = os.path.join(os.getcwd(), DEFAULT_DB)
        if os.path.exists(cwd_db_path):
            args.db = cwd_db_path
            print(f"Using default database: {args.db}")
        else:
            parser.error("No database file provided and 'cards.db' not found in the current directory.")

    if not os.path.isfile(args.db):
        print(f"Error: SQLite DB file not found at '{args.db}'")
        sys.exit(1)

    version = "0.1.1"
    if not args.skip_banner:
        if getattr(sys, 'frozen', False):  # Checks if running as a PyInstaller executable
            base_path = sys._MEIPASS
        else:
            base_path = os.getcwd()  # Normal execution, use current directory
        printer = CinematicLogoPrinter(os.path.join(base_path, 'logo.html'))
        printer.display_logo(delay=0.05)
        printer.print_line(f"Card Identify Server \033[1;35mv{str(version)}\033[0m", char_delay=0.03, sleep=0.5)
        printer.print_line("Created by \033[32mSideswipeeZ\033[0m", char_delay=0.03, sleep=0.5)
        printer.print_line("\033[36mKaiba Corp™\033[0m Mainframe. Loading", char_delay=0.03, sleep=0.5)
        printer.print_line("...", char_delay=0.33)
        printer.print_line("\033[36mKaiba Corp™\033[0m Mainframe. \033[1;32mLoaded.", char_delay=0.03, sleep=1)
        printer.print_line("\033[33mVirtual Systems Ready.\033[0m", char_delay=0.03)
        printer.print_line("".rjust(40, "*"), char_delay=0.03, sleep=0.5)
        printer.print_line(" ", char_delay=0.03, sleep=0.5)
    else:
        print("Starting without banner...")
        print(f"Card Identify Server \033[1;35mv{str(version)}\033[0m")
        print("Created by \033[32mSideswipeeZ\033[0m")

    nfc_reader = NFCReader(db_path=args.db, host=args.address, port=args.port)
    nfc_reader.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        sys.exit(nfc_reader.stop())
