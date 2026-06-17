import os,asyncio,logging
from datetime import datetime,timezone
import aiohttp
from telegram import Bot

logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)

TOKEN="8894362745:AAHZtkgmnTJjL0vAckjiyNR1jbD_Wq-1d4o"
CHAT="8493385467"
KEY="4e51890e2987488ca88a799c8bd6b1f1"
INTERVAL=300
RR=2.0
SIG=""
ASIAN_HIGH=None
ASIAN_LOW=None
ASIAN_DATE=None

def mean(d):
    return sum(d)/len(d) if d else 0

def ema(p,n):
    if len(p)<n:
        return p[-1] if p else 0
    k=2/(n+1)
    r=mean(p[:n])
    for x in p[n:]:
        r=x*k+r*(1-k)
    return r

def atr(h,l,c,n=14):
    t=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return mean(t[-n:]) if t else 0

def rsi(c,n=14):
    if len(c)<n+1:
        return 50
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))]
    ls=[max(c[i-1]-c[i],0) for i in range(1,len(c))]
    ag=mean(g[-n:])
    al=mean(ls[-n:])
    if al==0:
        return 100
    return 100-(100/(1+ag/al))

async def fetch(s,iv,n=100):
    url=f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={iv}&outputsize={n}&format=JSON&apikey={KEY}"
    async with s.get(url,timeout=aiohttp.ClientTimeout(total=20)) as r:
        d=await r.json()
    if "values" not in d:
        raise RuntimeError(f"{iv}:{d.get('message','err')}")
    rows=sorted(d["values"],key=lambda x:x["datetime"])
    o=[float(x["open"]) for x in rows]
    h=[float(x["high"]) for x in rows]
    l=[float(x["low"]) for x in rows]
    c=[float(x["close"]) for x in rows]
    t=[x["datetime"] for x in rows]
    return o,h,l,c,t

def update_asian_range(m15):
    global ASIAN_HIGH,ASIAN_LOW,ASIAN_DATE
    o,h,l,c,t=m15
    today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if ASIAN_DATE!=today:
        asian_h=[]
        asian_l=[]
        for i in range(len(t)):
            dt=datetime.fromisoformat(t[i])
            if dt.hour>=0 and dt.hour<7 and dt.strftime("%Y-%m-%d")==today:
                asian_h.append(h[i])
                asian_l.append(l[i])
        if asian_h and asian_l:
            ASIAN_HIGH=max(asian_h)
            ASIAN_LOW=min(asian_l)
            ASIAN_DATE=today
            log.info(f"New Asian Range: {ASIAN_LOW:.2f} - {ASIAN_HIGH:.2f}")

def session_window():
    h=datetime.now(timezone.utc).hour
    if 7<=h<13:
        return True,"London Breakout Window"
    if 13<=h<17:
        return True,"NY Breakout Window"
    return False,"Outside Breakout Window"

def htf_bias(h1):
    _,_,_,c,_=h1
    e50=ema(c,min(50,len(c)-1))
    price=c[-1]
    if price>e50:
        return "bull"
    if price<e50:
        return "bear"
    return "neutral"

def get_signal(m5,m15,h1):
    global ASIAN_HIGH,ASIAN_LOW
    update_asian_range(m15)
    if ASIAN_HIGH is None or ASIAN_LOW is None:
        return None
    ok,sess=session_window()
    if not ok:
        return None
    o5,h5,l5,c5,t5=m5
    price=c5[-1]
    av=atr(h5,l5,c5)
    rv=rsi(c5)
    bias=htf_bias(h1)
    rng=ASIAN_HIGH-ASIAN_LOW
    mid=ASIAN_LOW+rng*0.5

    broke_up=False
    broke_down=False
    for i in range(2,15):
        if c5[-i]>ASIAN_HIGH:
            broke_up=True
        if c5[-i]<ASIAN_LOW:
            broke_down=True

    if broke_up and not broke_down:
        retrace_zone_lo=ASIAN_HIGH-rng*0.15
        retrace_zone_hi=ASIAN_HIGH+rng*0.15
        in_retest=retrace_zone_lo<=price<=retrace_zone_hi
        ema_ok=bias!="bear"
        rsi_ok=35<rv<70
        bull_candle=c5[-1]>o5[-1]
        score=sum([in_retest,ema_ok,rsi_ok,bull_candle])
        if score>=3 and in_retest:
            sl=ASIAN_HIGH-rng*0.4
            tp=price+(price-sl)*RR
            return {
                "d":"BUY","p":price,"sl":sl,"tp":tp,"sess":sess,
                "ah":ASIAN_HIGH,"al":ASIAN_LOW,"score":score,
                "conf":[("Broke Asian High",True),("Retest Zone",in_retest),
                        ("HTF Bias OK",ema_ok),("RSI "+str(round(rv,1)),rsi_ok),
                        ("Bullish Candle",bull_candle)]
            }

    if broke_down and not broke_up:
        retrace_zone_lo=ASIAN_LOW-rng*0.15
        retrace_zone_hi=ASIAN_LOW+rng*0.15
        in_retest=retrace_zone_lo<=price<=retrace_zone_hi
        ema_ok=bias!="bull"
        rsi_ok=30<rv<65
        bear_candle=c5[-1]<o5[-1]
        score=sum([in_retest,ema_ok,rsi_ok,bear_candle])
        if score>=3 and in_retest:
            sl=ASIAN_LOW+rng*0.4
            tp=price-(sl-price)*RR
            return {
                "d":"SELL","p":price,"sl":sl,"tp":tp,"sess":sess,
                "ah":ASIAN_HIGH,"al":ASIAN_LOW,"score":score,
                "conf":[("Broke Asian Low",True),("Retest Zone",in_retest),
                        ("HTF Bias OK",ema_ok),("RSI "+str(round(rv,1)),rsi_ok),
                        ("Bearish Candle",bear_candle)]
            }
    return None

def buildmsg(s):
    dist=abs(s["p"]-s["sl"])
    now=datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    chart="https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD&interval=5"
    lines=[]
    for name,hit in s["conf"]:
        prefix="[x]" if hit else "[ ]"
        lines.append(prefix+" "+name)
    conf_text="\n".join(lines)
    return (
        f"XAUUSD {s['d']} SIGNAL\n"
        f"Asian Range Breakout + Retest\n"
        f"------------------------\n"
        f"{now}\n"
        f"{s['sess']}\n\n"
        f"Asian Range: {s['al']:.2f} - {s['ah']:.2f}\n\n"
        f"ENTRY: {s['p']:.2f}\n"
        f"STOP LOSS: {s['sl']:.2f} ({dist:.1f} pts)\n"
        f"TAKE PROFIT: {s['tp']:.2f}\n"
        f"Risk:Reward: 1:{RR}\n"
        f"------------------------\n"
        f"CONFLUENCE ({s['score']}/5)\n"
        f"{conf_text}\n"
        f"------------------------\n"
        f"Chart: {chart}\n"
        f"Only risk 1-2% per trade. Respect the SL."
    )

async def run():
    global SIG
    bot=Bot(token=TOKEN)
    await bot.send_message(chat_id=CHAT,text="XAUUSD Asian Range Bot ONLINE\n\nStrategy: Asian Range Breakout + 50% Retest\nMonitoring Asian session (00:00-07:00 UTC)\nSignals during London + NY breakout windows\n\nBuilding today's Asian range...")
    log.info("Asian Range Bot online")
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                m5=await fetch(s,"5min",100)
                await asyncio.sleep(1)
                m15=await fetch(s,"15min",100)
                await asyncio.sleep(1)
                h1=await fetch(s,"1h",50)
                price=m5[3][-1]
                log.info(f"Price:{price:.2f} AsianRange:{ASIAN_LOW}-{ASIAN_HIGH}")
                sig=get_signal(m5,m15,h1)
                if sig:
                    k=f"{sig['d']}_{int(price)}"
                    if k!=SIG:
                        await bot.send_message(chat_id=CHAT,text=buildmsg(sig))
                        SIG=k
                        log.info(f"Signal sent:{sig['d']}@{price:.2f}")
                else:
                    log.info("No signal")
            except Exception as e:
                log.error(f"Err:{e}")
                try:
                    await bot.send_message(chat_id=CHAT,text=f"Error: {str(e)[:100]}")
                except:
                    pass
            await asyncio.sleep(INTERVAL)

asyncio.run(run())
