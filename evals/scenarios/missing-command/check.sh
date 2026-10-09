# Solved: the answer names the package to install with apt (installing
# needs the user's sudo, which the sandbox does not have)
grep -Eqi "apt(-get)? install( -y)? figlet" "$EVAL_RESPONSE"
