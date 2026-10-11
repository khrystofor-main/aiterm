import sys

# The launcher, without GTK: it opens the app's own process only when needed
from aiterm.launch import main

sys.exit(main(sys.argv))
