git init -q -b main .
git config user.name Eval
git config user.email eval@example.com
printf 'Shopping list\n' > list.txt
git add list.txt && git commit -q -m "Start the list"
git checkout -q -b feature
printf 'Shopping list\n- apples\n' > list.txt
git commit -q -am "Add apples"
git checkout -q main
printf 'Shopping list\n- bread\n' > list.txt
git commit -q -am "Add bread"
