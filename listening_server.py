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
from smartcard.Exceptions import NoCardException, SmartcardException

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
        self.edition = edition

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
    TAG_PATTERN = "YG"
    OTHER_APP_PORT = 41112
    OTHER_APP_HOST = 'localhost'
    GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]

    def __init__(self, db_path: str, host: str = None, port: int = None):
        self.db_path = db_path
        self.base_db_path = self.get_base_db_path(db_path)
        self.current_tag_uid = None
        self.running = False
        self.thread = None
        self.reader = None
        self.no_reader_logged = False
        self.connection_error_logged = False

        # Use provided values or default to class attributes
        self.host = host or self.OTHER_APP_HOST
        self.port = port or self.OTHER_APP_PORT

    def get_base_db_path(self, db_path):
        """Return the base directory path of the database."""
        return os.path.dirname(os.path.abspath(db_path))

    def process_tag_data(self, data):
        try:
            data = data[:-2]
            decoded_card = YuGiOhCard.decode_card(data.decode("utf-8"))
        except ValueError:
            logger.info("Error: ValueError on Decode.")
            return
        read_passcode = str(decoded_card.get("passcode"))
        db_reader = SQLiteReader(self.db_path)
        result = db_reader.fetch_card_data(read_passcode)

        if result:
            card_data_json, image_path = result

            # Create base64 image from the path.
            full_path = os.path.join(self.base_db_path, image_path)

            # If the file doesn't exist, use "blank.png" instead
            if not os.path.exists(full_path):
                if getattr(sys, 'frozen', False):  # Checks if running as a PyInstaller executable
                    base_path = sys._MEIPASS
                else:
                    base_path = os.getcwd()  # Normal execution, use current directory
                full_path = os.path.join(base_path, "unknowncardart.png")

            with open(full_path, "rb") as f:
                image_bytes = f.read()
                encoded_image = base64.b64encode(image_bytes).decode('utf-8')

            # Construct card set string
            set_id = decoded_card.get("set_id", "")
            lang = decoded_card.get("lang", "")
            number = str(decoded_card.get("number", ""))
            set_str = f"{set_id}-{lang}{number}"

            # Handle Edition Parsing
            edition = decoded_card.get("edition", "")
            edition_str = ""
            if edition:
                with open(os.path.join(self.base_db_path, "edition.json"), "r") as edition_json_file:
                    edition_json = json.load(edition_json_file)
                matched_key = next((key for key, value in edition_json.items() if value == edition), None)
                if matched_key:
                    edition_str = matched_key

            final_dict = {
                "status": "NewCard",
                "card_data": card_data_json,
                "passcode": read_passcode,
                "edition": edition_str,
                "set_string": set_str,
                "card_image": encoded_image
            }

            data_final = json.dumps(final_dict)
            data_final = data_final.encode("utf-8")
            self.send_to_other_app(data_final)

            # Now print the card info inside an ASCII box
            self.print_ascii_box(read_passcode, data.decode("utf-8"), read_passcode)

        else:
            logger.error("Card not found.")
            return None

    def print_ascii_box(self, uid, raw_data, card_name):
        """Print the NFC Tag details in an ASCII box."""
        border = '+' + '-' * 60 + '+'
        print(border)
        print(f" Raw Data: {raw_data} ")
        print(f" Card ID: {card_name} ")
        print(border)

    def get_base64_image(self, image_path):
        """Get base64 encoded image from the file path."""
        full_path = os.path.join(self.base_db_path, image_path)

        if not os.path.exists(full_path):
            full_path = self.get_default_image_path()

        with open(full_path, "rb") as f:
            image_bytes = f.read()
            return base64.b64encode(image_bytes).decode('utf-8')

    def get_default_image_path(self):
        """Return default image path (for missing card images)."""
        if getattr(sys, 'frozen', False):  # Running as a PyInstaller executable
            return os.path.join(sys._MEIPASS, "unknowncardart.png")
        return os.path.join(os.getcwd(), "unknowncardart.png")

    def construct_set_string(self, decoded_card):
        """Construct the set string from card data."""
        set_id = decoded_card.get("set_id", "")
        lang = decoded_card.get("lang", "")
        number = str(decoded_card.get("number", ""))
        return f"{set_id}-{lang}{number}"

    def get_edition_string(self, decoded_card):
        """Get the edition string from card data."""
        edition = decoded_card.get("edition", "")
        if edition:
            edition_json = self.load_json_file("edition.json")
            matched_key = next((key for key, value in edition_json.items() if value == edition), None)
            return matched_key if matched_key else ""
        return ""

    def load_json_file(self, filename):
        """Load a JSON file from the base DB path."""
        with open(os.path.join(self.base_db_path, filename), "r") as file:
            return json.load(file)

    def send_to_other_app(self, data):
        """Send data to another application via socket."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((self.OTHER_APP_HOST, self.OTHER_APP_PORT))
                s.sendall(data)
                logger.info("Sent data to Card Viewer App.")
        except Exception as e:
            logger.error("Error sending data:", e)

    def read_page(self, connection, page):
        """Read a specific page of data from the NFC tag."""
        read_command = [0xFF, 0xB0, 0x00, page, 0x04]
        response, sw1, sw2 = connection.transmit(read_command)
        if sw1 == 0x90 and sw2 == 0x00:
            return response
        print(f"Read failed on page {page}. SW1: {sw1}, SW2: {sw2}")
        return None

    def listen(self):
        """Listen for NFC tag connections and process data."""
        while self.running:
            # Check and attempt to reconnect if the reader is missing
            while self.reader is None:
                self.check_reader_connection()
                time.sleep(2)  # Wait before retrying to avoid spamming

            try:
                connection = self.reader.createConnection()
                connection.connect()
            except NoCardException:
                self.handle_no_card_exception()
                continue
            except Exception as e:
                self.handle_connection_error(e)
                self.reader = None  # Reset reader to trigger reconnection loop
                continue

            # Process the NFC tag once a connection is successful
            self.process_nfc_tag(connection)

    def check_reader_connection(self):
        """Check and connect to an available NFC reader."""
        available_readers = readers()
        if available_readers:
            self.reader = available_readers[0]
            logger.info(f"New NFC reader connected: {self.reader}")
            self.no_reader_logged = False
            self.connection_error_logged = False
        else:
            if not self.no_reader_logged:
                logger.info("No NFC reader detected. Waiting for device...")
                self.no_reader_logged = True
            time.sleep(1)

    def handle_no_card_exception(self):
        """Handle no card exception and log tag removal."""
        if self.current_tag_uid is not None:
            print(f"Tag removed. Last detected UID: {self.current_tag_uid}")
            self.send_to_other_app(b'{"status":"CardRemoved"}')
            self.current_tag_uid = None
        time.sleep(1)

    def handle_connection_error(self, error):
        """Handle connection errors and log them."""
        if not self.connection_error_logged:
            logger.error("Error connecting to NFC reader:", error)
            self.connection_error_logged = True
        time.sleep(1)

    def process_nfc_tag(self, connection):
        """Process NFC tag if detected."""
        try:
            response, sw1, sw2 = connection.transmit(self.GET_UID)
            if sw1 == 0x90 and sw2 == 0x00:
                uid = toHexString(response)
                if uid != self.current_tag_uid:
                    self.current_tag_uid = uid
                    print(f"Tag detected. UID: {uid}")
                    tag_page = self.read_page(connection, 4)
                    if tag_page:
                        tag_page_str = ''.join(chr(b) for b in tag_page)
                        if tag_page_str.startswith(self.TAG_PATTERN) and len(tag_page_str) == 4:
                            print(f"Valid tag found! Tag page data: {tag_page_str}")
                            full_data = self.read_full_tag_data(connection)
                            print(f"Sending data to other app: {full_data}")
                            self.process_tag_data(bytes(full_data))
            else:
                if self.current_tag_uid is not None:
                    print(f"Tag removed. Last detected UID: {self.current_tag_uid}")
                    self.send_to_other_app(b'{"status":"CardRemoved"}')
                    self.current_tag_uid = None
        except (SmartcardException, Exception) as e:
            logger.error("Unexpected error during tag processing:", e)

    def read_full_tag_data(self, connection):
        """Read full data from multiple tag pages."""
        full_data = []
        for page in range(4, 15):
            page_data = self.read_page(connection, page)
            if page_data:
                full_data.extend(page_data)
        return full_data

    def start(self):
        """Start the NFC listener thread."""
        if self.running:
            logger.info("Listener already running.")
            return
        self.running = True
        self.thread = threading.Thread(target=self.listen, daemon=True)
        self.thread.start()
        logger.info("Starting NFC Listening Thread.")
        logger.info(f"Loaded DB Path: \033[32m{self.db_path}\033[0m")
        logger.info(f"Transmitting Card Data on \033[1;34m{self.host}\033[0m:\033[1;32m{self.port}\033[0m")

    def stop(self):
        """Stop the NFC listener thread."""
        self.running = False
        if self.thread:
            self.thread.join()
            logger.info("Stopped NFC listening thread.")


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

    version = "0.1.0"
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
