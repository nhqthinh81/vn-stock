from vnstock import Vnstock
s = Vnstock().stock('HPG', source='VCI')

print('=== NEWS ===')
try:
    n = s.company.news()
    print(list(n.columns))
    print(n[['published_date','title','source']].head(3).to_string())
except Exception as e: print('news error:', e)

print('=== EVENTS ===')
try:
    ev = s.company.events()
    print(list(ev.columns))
    print(ev.head(3).to_string())
except Exception as e: print('events error:', e)

print('=== DIVIDENDS ===')
try:
    dv = s.company.dividends()
    print(list(dv.columns))
    print(dv.head(3).to_string())
except Exception as e: print('dividends error:', e)

print('=== INSIDER ===')
try:
    ins = s.company.insider_deals()
    print(list(ins.columns))
    print(ins.head(3).to_string())
except Exception as e: print('insider error:', e)
