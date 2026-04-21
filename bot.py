import os, requests, logging
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DART_API_KEY   = os.environ.get("DART_API_KEY", "")
DART_BASE      = "https://opendart.fss.or.kr/api"
YEAR           = str(datetime.now().year - 1)

def dart_get(path, params):
    params["crtfc_key"] = DART_API_KEY
    r = requests.get(f"{DART_BASE}/{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def search_company(name):
    data = dart_get("company.json", {"corp_name": name, "page_count": 20})
    return [c for c in data.get("list", []) if c.get("stock_code", "").strip()][:5]

def get_financials(corp_code):
    data = dart_get("fnlttSinglAcntAll.json", {
        "corp_code": corp_code, "bsns_year": YEAR,
        "reprt_code": "11011", "fs_div": "OFS",
    })
    return data.get("list", [])

def find_val(items, field, *keywords):
    for kw in keywords:
        for item in items:
            if kw.replace(" ","") in item.get("account_nm","").replace(" ",""):
                v = item.get(field,"").replace(",","")
                if v:
                    try: return float(v)
                    except: pass
    return None

def g(items, *k): return find_val(items, "thstrm_amount", *k)
def p(items, *k): return find_val(items, "frmtrm_amount", *k)
def pct(a,b): return None if not(a and b and b!=0) else a/b*100
def fmt_bil(v): return "없음" if v is None else f"{round(v/1e8):,}억원"
def fv(v, u="%"): return "없음" if v is None else f"{v:.1f}{u}"
def ci(ok): return "?" if ok is None else ("OK" if ok else "NG")

def analyze(items):
    rev  = g(items,"매출액","수익(매출액)","영업수익")
    prev = p(items,"매출액","수익(매출액)","영업수익")
    op   = g(items,"영업이익","영업손익")
    net  = g(items,"당기순이익","당기순손익")
    liab = g(items,"부채총계")
    eq   = g(items,"자본총계")
    ca   = g(items,"유동자산")
    cl   = g(items,"유동부채")
    ocf  = g(items,"영업활동현금흐름","영업활동으로인한현금흐름")
    iexp = g(items,"이자비용","금융원가")
    return dict(
        rev=rev, op=op, net=net, eq=eq, ocf=ocf,
        debt=pct(liab,eq), cur=pct(ca,cl), margin=pct(op,rev),
        growth=pct(rev-prev,abs(prev)) if rev and prev and prev!=0 else None,
        icov=abs(op/iexp) if op and iexp and iexp!=0 else None,
        gap=abs((net-op)/abs(op)*100) if net and op and op!=0 else None,
    )

def build_report(name, stock, a):
    c = {
        "g": a["growth"] is not None and a["growth"]>0,
        "m": a["margin"] is not None and a["margin"]>=10,
        "n": a["gap"]    is not None and a["gap"]<=30,
        "o": a["ocf"]    is not None and a["ocf"]>0,
        "d": a["debt"]   is not None and a["debt"]<=200,
        "r": a["cur"]    is not None and a["cur"]>=100,
        "i": a["icov"]   is not None and a["icov"]>=3,
        "e": a["eq"]     is not None and a["eq"]>0,
    }
    ok=sum(1 for v in c.values() if v); tot=len(c)
    pct_s=round(ok/tot*100) if tot else 0
    verdict=("우량 - 심층 분석 후 투자 검토 가능" if pct_s>=75 else
             "보통 - 취약 항목 추가 확인 필요" if pct_s>=50 else
             "미흡 - 현 조건으로 투자 리스크 큼")
    url=f"https://dart.fss.or.kr/dsab007/main.do?autoSearch=true&textCrpNm={requests.utils.quote(name)}"
    return "\n".join([
        f"[기업분석] {name} ({stock})", f"기준: {YEAR}년 사업보고서", "",
        "=== 핵심 재무지표 ===",
        f"  매출액:     {fmt_bil(a['rev'])}",
        f"  영업이익:   {fmt_bil(a['op'])}",
        f"  영업이익률: {fv(a['margin'])}",
        f"  부채비율:   {fv(a['debt'])}", "",
        "=== 수익성 ===",
        f"  매출 성장: {fv(a['growth'])} [{ci(c['g'])}]",
        f"  영업이익률 10%이상: {fv(a['margin'])} [{ci(c['m'])}]",
        f"  영업순이익 괴리: {fv(a['gap'])} [{ci(c['n'])}]",
        f"  영업 현금흐름: {fmt_bil(a['ocf'])} [{ci(c['o'])}]", "",
        "=== 안정성 ===",
        f"  부채비율 200%이하: {fv(a['debt'])} [{ci(c['d'])}]",
        f"  유동비율 100%이상: {fv(a['cur'])} [{ci(c['r'])}]",
        f"  이자보상배율 3배이상: {fv(a['icov'],'배')} [{ci(c['i'])}]",
        f"  자본총계 플러스: {fmt_bil(a['eq'])} [{ci(c['e'])}]", "",
        "=== 수동 확인 필요 ===",
        "  감사의견 적정 여부", "  임원 지분 매매 동향",
        "  횡령배임 공시 여부", "  유상증자 빈도", "",
        "=== 종합 점수 ===",
        f"  자동분석: {ok}/{tot} 항목 통과", f"  {verdict}", "",
        f"DART 공시: {url}", "",
        "본 분석은 참고용이며 투자 권유가 아닙니다.",
    ])

async def cmd_start(update, context):
    await update.message.reply_text("안녕하세요! DART 기업분석 봇입니다.\n\n분석할 기업명을 보내주세요.\n예: 삼성전자 / 카카오 / 현대차")

async def handle(update, context):
    query = update.message.text.strip()
    corp_list = context.user_data.get("corp_list")
    if corp_list and query.isdigit():
        idx = int(query)-1
        if 0 <= idx < len(corp_list):
            context.user_data.pop("corp_list", None)
            await fetch_send(update, corp_list[idx]); return
    await update.message.reply_text(f"'{query}' 검색 중...")
    try: corps = search_company(query)
    except Exception as e:
        await update.message.reply_text(f"검색 오류: {e}"); return
    if not corps:
        await update.message.reply_text("상장 기업을 찾지 못했습니다."); return
    if len(corps) > 1:
        lines = ["여러 기업이 검색됐습니다. 번호를 입력하세요:\n"]
        for i,c in enumerate(corps,1): lines.append(f"{i}. {c['corp_name']} ({c['stock_code']})")
        context.user_data["corp_list"] = corps
        await update.message.reply_text("\n".join(lines)); return
    await fetch_send(update, corps[0])

async def fetch_send(update, corp):
    name,code,stock = corp["corp_name"],corp["corp_code"],corp["stock_code"]
    await update.message.reply_text(f"{name} 데이터 불러오는 중...")
    try: items = get_financials(code)
    except Exception as e:
        await update.message.reply_text(f"DART 오류: {e}"); return
    if not items:
        await update.message.reply_text(f"{name}의 {YEAR}년 데이터가 없습니다."); return
    report = build_report(name, stock, analyze(items))
    await update.message.reply_text(report, disable_web_page_preview=True)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()

if __name__ == "__main__":
    main()
