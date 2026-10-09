# Solved: the merge is committed, with both lines and no conflict markers
[ "$(cat list.txt)" = "$(printf 'Shopping list\n- bread\n- apples')" ] &&
  [ -n "$(git log --merges --oneline)" ] && [ -z "$(git status --porcelain)" ]
