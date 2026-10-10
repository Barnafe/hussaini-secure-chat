"""Start the Hussaini Secure Chat desktop client (Tkinter).

    python run_client.py

The server address (host:port) is typed in the login window. Its starting value
comes from the 'server_address' line in settings.txt.
"""
import sys

from client.main import main

if __name__ == "__main__":
    sys.exit(main())
