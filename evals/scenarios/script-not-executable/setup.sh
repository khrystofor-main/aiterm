printf '#!/bin/sh\necho "deployed at $(date +%%s)" > deployed.txt\necho Deployed.\n' > deploy.sh
chmod 644 deploy.sh
