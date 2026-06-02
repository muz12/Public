"""
AI Neural Trend — Bot de Trading Automático
Corretora: Binance Futures Testnet
Risco: 1% do saldo por trade
TP e SL calculados automaticamente pelo bot
"""

from flask import Flask, request, jsonify
from binance.um_futures import UMFutures
from binance.error import ClientError
import math, os, logging

# ─── CONFIGURAÇÃO ────────────────────────────────────────────
API_KEY        = os.getenv("BINANCE_API_KEY",    "2br67w7BZFgHqODtMC0lRQieqBgNzf1GdIvSZKkpRPOZlwBlw7IGFF54BgyA7ZwE")
API_SECRET     = os.getenv("BINANCE_API_SECRET", "orFZ8edXd0D39M6qq2dtqYlV9903PfhCd3PWAQWCk6rSpTkpBJq9Rfo0TwocTw38")
RISK_PCT       = 0.02        # 1% do saldo por trade
SYMBOL         = "BTCUSDT"
LEVERAGE       = 5
WEBHOOK_SECRET = ""

# TP e SL em % do preço (quando não vêm no webhook)
TP_PCT = 0.015   # 2% de take profit
SL_PCT = 0.005   # 1% de stop loss

# ─── SETUP ───────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

client = UMFutures(
    key      = API_KEY,
    secret   = API_SECRET,
    base_url = "https://testnet.binancefuture.com"
)

# ─── FUNÇÕES ─────────────────────────────────────────────────

def get_balance():
    try:
        balances = client.balance()
        for b in balances:
            if b["asset"] == "USDT":
                balance = float(b["availableBalance"])
                log.info(f"Saldo: {balance} USDT")
                return balance
        return 0.0
    except Exception as e:
        log.error(f"Erro saldo: {e}")
        return 0.0

def get_price(symbol):
    try:
        ticker = client.ticker_price(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        log.error(f"Erro preço: {e}")
        return 0.0

def get_lot_size(symbol):
    try:
        info = client.exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        return float(f["stepSize"]), float(f["minQty"])
        return 0.001, 0.001
    except Exception as e:
        log.error(f"Erro lot size: {e}")
        return 0.001, 0.001

def round_step(qty, step):
    precision = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(math.floor(qty / step) * step, precision)

def calc_qty(balance, price, symbol):
    risk_usdt     = balance * RISK_PCT
    raw_qty       = risk_usdt / price
    step, min_qty = get_lot_size(symbol)
    qty           = round_step(raw_qty, step)
    qty           = max(qty, min_qty)
    log.info(f"Risco: {risk_usdt:.2f} USDT | Qty: {qty}")
    return qty

def calc_tp_sl(side, price, tp=0, sl=0):
    """
    Se TP e SL vierem a 0 no webhook,
    calcula automaticamente com base em % do preço.
    """
    if tp == 0 or sl == 0:
        if side == "BUY":
            tp = round(price * (1 + TP_PCT), 2)
            sl = round(price * (1 - SL_PCT), 2)
        else:
            tp = round(price * (1 - TP_PCT), 2)
            sl = round(price * (1 + SL_PCT), 2)
        log.info(f"TP/SL calculados automaticamente | TP: {tp} | SL: {sl}")
    else:
        log.info(f"TP/SL recebidos do webhook | TP: {tp} | SL: {sl}")
    return tp, sl

def set_leverage(symbol, leverage):
    try:
        client.change_leverage(symbol=symbol, leverage=leverage)
    except Exception as e:
        log.warning(f"Erro alavancagem: {e}")

def close_existing_position(symbol, side):
    try:
        positions = client.get_position_risk(symbol=symbol)
        for pos in positions:
            amt = float(pos.get("positionAmt", 0))
            if amt != 0:
                if side == "BUY" and amt < 0:
                    client.new_order(symbol=symbol, side="BUY", type="MARKET",
                                     quantity=abs(amt), reduceOnly="true")
                    log.info("Posição SHORT fechada")
                elif side == "SELL" and amt > 0:
                    client.new_order(symbol=symbol, side="SELL", type="MARKET",
                                     quantity=abs(amt), reduceOnly="true")
                    log.info("Posição LONG fechada")
    except Exception as e:
        log.warning(f"Erro fechar posição: {e}")

def place_order(side, symbol, tp=0, sl=0):
    balance = get_balance()
    if balance <= 0:
        return {"error": "sem saldo"}

    price = get_price(symbol)
    if price <= 0:
        return {"error": "preço inválido"}

    # Calcula TP e SL automaticamente se vierem a 0
    tp, sl = calc_tp_sl(side, price, tp, sl)

    qty = calc_qty(balance, price, symbol)
    set_leverage(symbol, LEVERAGE)
    close_existing_position(symbol, side)

    try:
        order = client.new_order(
            symbol   = symbol,
            side     = side,
            type     = "MARKET",
            quantity = qty,
        )
        log.info(f"✅ Ordem {side} | {qty} {symbol} | Preço: {price}")

        # Take Profit
        tp_side = "SELL" if side == "BUY" else "BUY"
        client.new_order(
            symbol        = symbol,
            side          = tp_side,
            type          = "TAKE_PROFIT_MARKET",
            stopPrice     = str(tp),
            closePosition = "true",
            timeInForce   = "GTE_GTC",
        )
        log.info(f"✅ TP definido: {tp} ({TP_PCT*100}%)")

        # Stop Loss
        sl_side = "SELL" if side == "BUY" else "BUY"
        client.new_order(
            symbol        = symbol,
            side          = sl_side,
            type          = "STOP_MARKET",
            stopPrice     = str(sl),
            closePosition = "true",
            timeInForce   = "GTE_GTC",
        )
        log.info(f"✅ SL definido: {sl} ({SL_PCT*100}%)")

        return {
            "status" : "ok",
            "side"   : side,
            "symbol" : symbol,
            "qty"    : qty,
            "price"  : price,
            "tp"     : tp,
            "sl"     : sl
        }

    except ClientError as e:
        log.error(f"❌ Erro Binance: {e}")
        return {"error": str(e)}
    except Exception as e:
        log.error(f"❌ Erro: {e}")
        return {"error": str(e)}

# ─── WEBHOOK ─────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "sem dados"}), 400

    secret = request.headers.get("X-Secret", "")
    if secret != WEBHOOK_SECRET:
        log.warning("Secret inválido!")
        return jsonify({"error": "não autorizado"}), 403

    log.info(f"Webhook recebido: {data}")

    side   = data.get("side", "").upper()
    symbol = data.get("symbol", SYMBOL).replace(".P", "").replace("/", "")
    tp     = float(data.get("tp", 0))
    sl     = float(data.get("sl", 0))

    if side not in ("BUY", "SELL"):
        return jsonify({"error": f"side inválido: {side}"}), 400

    result = place_order(side, symbol, tp, sl)
    return jsonify(result), 200

@app.route("/health", methods=["GET"])
def health():
    balance = get_balance()
    price   = get_price(SYMBOL)
    return jsonify({
        "status"          : "online",
        "saldo_usdt"      : balance,
        "btc_price"       : price,
        "exchange"        : "Binance Futures Testnet",
        "symbol"          : SYMBOL,
        "risco_por_trade" : f"{RISK_PCT*100}%",
        "take_profit"     : f"{TP_PCT*100}%",
        "stop_loss"       : f"{SL_PCT*100}%"
    })

@app.route("/", methods=["GET"])
def index():
    return "🤖 AI Neural Trend Bot — Online", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    log.info(f"Bot a iniciar | Binance Futures Testnet | TP: {TP_PCT*100}% | SL: {SL_PCT*100}%")
    app.run(host="0.0.0.0", port=port)
