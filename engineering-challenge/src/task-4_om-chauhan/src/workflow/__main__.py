"""
Direct execution entrypoint: python -m workflow
"""

import sys
from workflow.cli import main

if __name__ == "__main__":
    sys.exit(main())
