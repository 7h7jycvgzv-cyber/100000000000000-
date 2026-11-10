# Python Lesson 1: 100兆円を計算する
# 実行方法: python3 python/lesson1_goal.py

# Step 1: 表示する
print("100兆円への記録")

# Step 2: 変数(名前をつけた箱)
goal = 100_000_000_000_000  # 目標: 100兆円
money = 10_000_000          # 元手: 1,000万円
rate = 1.3
print(goal)
print(goal / money)         # 何倍にすれば届くか

# Step 3: くり返し
# 毎年2倍になったら、何年で100兆円に届くか
year = 0
while money < goal:
    money = money * rate
    year = year + 1

print(year, "年で到達")
