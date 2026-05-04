[English](README.md) | [繁體中文](README_zh.md)

# CLI-Eh-Downloader

A command-line based gallery downloader for E-Hentai and ExHentai with an interactive shell interface.

![Interface](./demo/Interface.png)
*Interactive shell interface*

![Command List](./demo/CommandList.png)
*Command list and execution*

## Features

- **Interactive Shell:** An intuitive, rich-text command-line interface.
- **Smart Downloads:** Supports standard gallery downloads and automatic torrent downloads (via embedded downloader or passing to external system clients like qBittorrent).
- **Configurable:** Fully customizable settings (download directory, parallel tasks, cookies).
- **Auto-Environment Setup:** Easy one-click setup using the provided `.bat` file for Windows.

## Installation & Setup

### Prerequisites

- **Python 3.11** or higher.
- (Optional) `libtorrent` for embedded torrent downloading. If not installed, the application will save the `.torrent` file and attempt to open it with your system's default client (e.g., qBittorrent).

### Quick Start (Windows)

1. Clone or download this repository.
2. Double-click **`CLI-Eh-Downloader.bat`**.
3. If it's your first time, the script will prompt you to automatically create a virtual environment (`venv`) and install all required dependencies. Type `Y` to confirm.
4. The interactive shell will launch automatically.

### Manual Setup (All Platforms)

1. Create a virtual environment and activate it:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
2. Install the package and dependencies:
   ```bash
   pip install -e .
   ```
3. Start the application:
   ```bash
   ehdl
   # Or using Python directly: python -m cli_eh_downloader
   ```

## Usage

You can use the interactive shell or pass commands directly:

```bash
# Start interactive shell
ehdl

# Download a specific gallery directly
ehdl <gallery_url>

# Force download via torrent
ehdl <gallery_url> --torrent
```

In the interactive shell, type `help` to see all available commands.

## Configuration

The application generates a configuration file at `~/.config/cli-eh-downloader/config.toml` (or `config.toml` in the current directory if it exists). You can define your download directories, limits, and authentication cookies there.

## Disclaimer

**For Academic and Educational Purposes Only.** 
This tool is developed solely for the purpose of learning Python, API interaction, and command-line interface design. The author does not condone or encourage the unauthorized downloading or distribution of copyrighted material. By using this software, you take full responsibility for your actions and ensure that your usage complies with all applicable local laws and the Terms of Service of the respective websites.
