# Yu-Gi-Oh! NFC Card Server

This is a console-based server for the Yu-Gi-Oh! Card NFC project. It allows you to read NFC tags placed under Yu-Gi-Oh! cards and retrieve the corresponding card details stored in a database. The server is configurable via command-line arguments.

## Requirements

- Python 3.10+
- `bs4` (BeautifulSoup for parsing HTML)
- `pyscard` (for NFC reader support)

You can install the required dependencies with:

```bash
pip install -r requirements.txt
```

## Command-Line Arguments

The server accepts the following command-line arguments:

### `--db`

Specify the path to the SQLite database file.

```bash
--db /path/to/database.db
```

### `--skip-banner`

Skip printing the banner and delayed text during startup.

```bash
--skip-banner
```

### `--port`

Set the port for the NFC reader server. The default port is `41112`.

```bash
--port 41112
```

### `--address`

Set the address for the NFC reader server. The default address is `localhost`. This is the address for the Card Viewer or other app to get its data from.

```bash
--address localhost
```

## Example Usage

To start the server with a custom database and port, run the following command:

```bash
python card_server.py --db /path/to/database.db --port 41112 --address localhost
```

If you want to skip the startup banner, use the `--skip-banner` argument:

```bash
python card_server.py --db /path/to/database.db --skip-banner
```

## How It Works

This server listens for NFC reader events and looks up the card data from the specified SQLite database. When an NFC tag is scanned, the corresponding card information is retrieved and returned.

For ease of use, you can download the executable from the Releases page and place it into the same directory as the cards.db file and run.

## License

This project is licensed under the GPL 3.0 License - see the [LICENSE](LICENSE) file for details.
```
