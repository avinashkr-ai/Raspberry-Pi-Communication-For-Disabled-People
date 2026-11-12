import sys
from .app import main
from .vision import AssistantError

try:
    sys.exit(main())
except AssistantError as error:
    print(str(error), file=sys.stderr)
    sys.exit(1)
except KeyboardInterrupt:
    sys.exit(0)
