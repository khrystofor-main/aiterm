# Solved: the script has Unix line endings and ran
! grep -q $'\r' build.sh && grep -qx "Build OK" build.log
