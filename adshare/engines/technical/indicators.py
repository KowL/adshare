"""57 Technical Indicators Engine.

All indicators are implemented using pure pandas/numpy, without dependency on
AmazingData's TimeSeriesFunction. This allows the engine to run on any platform.
"""

from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ================================================================
#  Helper Functions (replacing AmazingData's TimeSeriesFunction)
# ================================================================

def _ma(series: pd.Series, n: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=n, min_periods=1).mean()


def _ema(series: pd.Series, n: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=n, adjust=False, min_periods=1).mean()


def _sma(series: pd.Series, n: int, m: int = 1) -> pd.Series:
    """SMA: today's SMA = (M * today's value + (N-M) * yesterday's SMA) / N."""
    alpha = m / n
    return series.ewm(alpha=alpha, adjust=False, min_periods=1).mean()


def _exp_mema(series: pd.Series, n: int) -> pd.Series:
    """Exponential MEMA (same as EMA)."""
    return _ema(series, n)


def _llv(series: pd.Series, n: int) -> pd.Series:
    """Lowest low value over N periods."""
    return series.rolling(window=n, min_periods=1).min()


def _hhv(series: pd.Series, n: int) -> pd.Series:
    """Highest high value over N periods."""
    return series.rolling(window=n, min_periods=1).max()


def _ref(series: pd.Series, n: int) -> pd.Series:
    """Reference (shift)."""
    return series.shift(n)


def _sum(series: pd.Series, n: int) -> pd.Series:
    """Rolling sum."""
    return series.rolling(window=n, min_periods=1).sum()


def _cumsum(series: pd.Series) -> pd.Series:
    """Cumulative sum."""
    return series.cumsum()


def _count(cond: pd.Series, n: int) -> pd.Series:
    """Count True values over N periods."""
    return cond.rolling(window=n, min_periods=1).sum()


def _std(series: pd.Series, n: int) -> pd.Series:
    """Rolling standard deviation."""
    return series.rolling(window=n, min_periods=1).std()


def _avedev(series: pd.Series, n: int) -> pd.Series:
    """Average absolute deviation."""
    return series.rolling(window=n, min_periods=1).apply(
        lambda x: np.mean(np.abs(x - np.mean(x))), raw=True
    )


def _slope(series: pd.Series, n: int) -> pd.Series:
    """Linear regression slope over N periods."""
    x = np.arange(n, dtype=float)

    def calc_slope(window):
        y = np.array(window, dtype=float)
        if len(y) < n or np.isnan(y).any():
            return np.nan
        return np.polyfit(x, y, 1)[0]

    return series.rolling(window=n, min_periods=n).apply(calc_slope, raw=False)


# ================================================================
#  TechnicalIndicators Class
# ================================================================

class TechnicalIndicators:
    """57 technical indicators implemented in pure pandas/numpy."""

    # ================================================================
    #  一、超买超卖型 (14 indicators)
    # ================================================================

    @staticmethod
    def KDJ(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, pd.Series]:
        """KDJ stochastic oscillator."""
        llv = _llv(low, n)
        hhv = _hhv(high, n)
        denom = hhv - llv
        denom = denom.replace(0, np.nan)
        rsv = (close - llv) / denom * 100
        k = _sma(rsv, m1, 1)
        d = _sma(k, m2, 1)
        j = 3 * k - 2 * d
        return {'K': k, 'D': d, 'J': j}

    @staticmethod
    def RSI(close: pd.Series, n1: int = 6, n2: int = 12, n3: int = 24) -> Dict[str, pd.Series]:
        """RSI relative strength index."""
        lc = _ref(close, 1)
        diff = close - lc
        pos_diff = np.maximum(diff, 0)
        abs_diff = diff.abs()
        return {
            f'RSI{n1}': _sma(pos_diff, n1, 1) / _sma(abs_diff, n1, 1) * 100,
            f'RSI{n2}': _sma(pos_diff, n2, 1) / _sma(abs_diff, n2, 1) * 100,
            f'RSI{n3}': _sma(pos_diff, n3, 1) / _sma(abs_diff, n3, 1) * 100,
        }

    @staticmethod
    def WR(close: pd.Series, high: pd.Series, low: pd.Series, n1: int = 10, n2: int = 6) -> Dict[str, pd.Series]:
        """Williams %R."""
        result = {}
        for n in [n1, n2]:
            hhv = _hhv(high, n)
            llv = _llv(low, n)
            denom = hhv - llv
            denom = denom.replace(0, np.nan)
            result[f'WR{n}'] = (hhv - close) / denom * 100
        return result

    @staticmethod
    def CCI(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 14) -> Dict[str, pd.Series]:
        """Commodity Channel Index."""
        typ = (high + low + close) / 3
        cci = (typ - _ma(typ, n)) * 1000 / (15 * _avedev(typ, n))
        return {'CCI': cci}

    @staticmethod
    def ROC(close: pd.Series, n: int = 12, m: int = 6) -> Dict[str, pd.Series]:
        """Rate of Change."""
        ref_close = _ref(close, n)
        roc = (close - ref_close) / ref_close * 100
        maroc = _ma(roc, m)
        return {'ROC': roc, 'MAROC': maroc}

    @staticmethod
    def MTM(close: pd.Series, n: int = 12, m: int = 6) -> Dict[str, pd.Series]:
        """Momentum."""
        mtm = close - _ref(close, n)
        mamtm = _ma(mtm, m)
        return {'MTM': mtm, 'MAMTM': mamtm}

    @staticmethod
    def BIAS(close: pd.Series, n1: int = 6, n2: int = 12, n3: int = 24) -> Dict[str, pd.Series]:
        """Bias ratio."""
        result = {}
        for n in [n1, n2, n3]:
            ma = _ma(close, n)
            result[f'BIAS{n}'] = (close - ma) / ma * 100
        return result

    @staticmethod
    def SKDJ(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 9, m: int = 3) -> Dict[str, pd.Series]:
        """Slow KDJ."""
        lowv = _llv(low, n)
        highv = _hhv(high, n)
        denom = highv - lowv
        denom = denom.replace(0, np.nan)
        rsv = _ema((close - lowv) / denom * 100, m)
        k = _ema(rsv, m)
        d = _ma(k, m)
        return {'K': k, 'D': d}

    @staticmethod
    def MFI(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, n: int = 14, n2: int = 6) -> Dict[str, pd.Series]:
        """Money Flow Index."""
        typ = (high + low + close) / 3
        mr = typ * volume
        ref_typ = _ref(typ, 1)
        pmf = _sum(np.where(typ > ref_typ, mr, 0), n)
        nmf = _sum(np.where(typ < ref_typ, mr, 0), n)
        denom = pd.Series(nmf, index=close.index).replace(0, np.nan)
        mfi = 100 - (100 / (1 + pd.Series(pmf, index=close.index) / denom))
        mfi.loc[(pd.Series(pmf, index=close.index) > 0) & (pd.Series(nmf, index=close.index) == 0)] = 100
        mfi.loc[(pd.Series(pmf, index=close.index) == 0) & (pd.Series(nmf, index=close.index) == 0)] = 50
        return {'MFI': mfi}

    @staticmethod
    def OSC(close: pd.Series, n: int = 20, m: int = 6) -> Dict[str, pd.Series]:
        """Oscillator."""
        osc = (close - _ma(close, n)) * 100
        maosc = _ema(osc, m)
        return {'OSC': osc, 'MAOSC': maosc}

    @staticmethod
    def UDL(close: pd.Series, n1: int = 3, n2: int = 5, n3: int = 10, n4: int = 20, m: int = 6) -> Dict[str, pd.Series]:
        """UDL gravity line."""
        udl = (_ma(close, n1) + _ma(close, n2) + _ma(close, n3) + _ma(close, n4)) / 4
        maudl = _ma(udl, m)
        return {'UDL': udl, 'MAUDL': maudl}

    @staticmethod
    def ACCER(close: pd.Series, n: int = 8) -> Dict[str, pd.Series]:
        """Acceleration rate."""
        slope_series = _slope(close, n)
        accer = slope_series / close
        return {'ACCER': accer}

    @staticmethod
    def RCCD(close: pd.Series, n: int = 59, short: int = 26, long: int = 52, m: int = 26) -> Dict[str, pd.Series]:
        """RCCD difference bias."""
        rc = close / _ref(close, n)
        arc = _sma(_ref(rc, 1), n, 1)
        dif = _ma(arc, short) - _ma(arc, long)
        rccd = _sma(dif, m, 1)
        return {'DIF': dif, 'RCCD': rccd}

    @staticmethod
    def MARSI(close: pd.Series, m1: int = 10, m2: int = 6) -> Dict[str, pd.Series]:
        """Moving Average RSI."""
        lc = _ref(close, 1)
        diff = close - lc
        vu = np.where(diff >= 0, diff, 0)
        vd = np.where(diff < 0, -diff, 0)
        mau1 = _ema(pd.Series(vu, index=close.index), m1)
        mad1 = _ema(pd.Series(vd, index=close.index), m1)
        mau2 = _ema(pd.Series(vu, index=close.index), m2)
        mad2 = _ema(pd.Series(vd, index=close.index), m2)
        rsi1_raw = 100 * mau1 / (mau1 + mad1)
        rsi2_raw = 100 * mau2 / (mau2 + mad2)
        rsi1 = _ma(rsi1_raw, m1)
        rsi2 = _ma(rsi2_raw, m2)
        return {'RSI1': rsi1, 'RSI2': rsi2}

    # ================================================================
    #  二、趋势型 (14 indicators)
    # ================================================================

    @staticmethod
    def MACD(close: pd.Series, short: int = 12, long: int = 26, mid: int = 9) -> Dict[str, pd.Series]:
        """MACD."""
        dif = _ema(close, short) - _ema(close, long)
        dea = _ema(dif, mid)
        macd = 2 * (dif - dea)
        return {'DIF': dif, 'DEA': dea, 'MACD': macd}

    @staticmethod
    def DMI(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 14, m: int = 6) -> Dict[str, pd.Series]:
        """Directional Movement Index."""
        ref_high = _ref(high, 1)
        ref_low = _ref(low, 1)
        ref_close = _ref(close, 1)
        tr1 = high - low
        tr2 = (high - ref_close).abs()
        tr3 = (ref_close - low).abs()
        mtr_unit = np.maximum(np.maximum(tr1, tr2), tr3)
        mtr = _sum(pd.Series(mtr_unit, index=close.index), n).replace(0, np.nan)
        hd = high - ref_high
        ld = ref_low - low
        dmp_raw = np.where((hd > 0) & (hd > ld), hd, 0)
        dmm_raw = np.where((ld > 0) & (ld > hd), ld, 0)
        dmp = _sum(pd.Series(dmp_raw, index=close.index), n)
        dmm = _sum(pd.Series(dmm_raw, index=close.index), n)
        pdi = dmp * 100 / mtr
        mdi = dmm * 100 / mtr
        denom = (mdi + pdi).replace(0, np.nan)
        dx = (mdi - pdi).abs() / denom * 100
        adx = _ma(dx, m)
        adxr = (adx + _ref(adx, m)) / 2
        return {'PDI': pdi, 'MDI': mdi, 'ADX': adx, 'ADXR': adxr}

    @staticmethod
    def DMA(close: pd.Series, n1: int = 10, n2: int = 50, m: int = 10) -> Dict[str, pd.Series]:
        """Different of Moving Average."""
        dif = _ma(close, n1) - _ma(close, n2)
        difma = _ma(dif, m)
        return {'DIF': dif, 'AMA': difma}

    @staticmethod
    def TRIX(close: pd.Series, n: int = 12, m: int = 9) -> Dict[str, pd.Series]:
        """Triple Exponential Moving Average."""
        mtr = _ema(_ema(_ema(close, n), n), n)
        ref_mtr = _ref(mtr, 1)
        trix = (mtr - ref_mtr) / ref_mtr * 100
        matrix = _ma(trix, m)
        return {'TRIX': trix, 'MATRIX': matrix}

    @staticmethod
    def ARBR(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, n: int = 26) -> Dict[str, pd.Series]:
        """ARBR (popularity & willingness)."""
        ar = _sum(high - open_, n) / _sum(open_ - low, n) * 100
        ref_close = _ref(close, 1)
        zero = close * 0
        br = _sum(np.maximum(high - ref_close, 0), n) / _sum(np.maximum(ref_close - low, 0), n) * 100
        return {'AR': ar, 'BR': br}

    @staticmethod
    def EMV(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, n: int = 14, m: int = 9) -> Dict[str, pd.Series]:
        """Ease of Movement."""
        vol_ratio = _ma(volume, n) / volume
        high_plus_low = high + low
        mid = 100 * (high + low - _ref(high_plus_low, 1)) / (high + low)
        hl = high - low
        emv = _ma(mid * vol_ratio * hl / _ma(hl, n), n)
        maemv = _ma(emv, m)
        return {'EMV': emv, 'MAEMV': maemv}

    @staticmethod
    def DPO(close: pd.Series, n: int = 20, m: int = 6) -> Dict[str, pd.Series]:
        """Detrended Price Oscillator."""
        ma_close = _ma(close, n)
        dpo = close - _ref(ma_close, n // 2 + 1)
        madpo = _ma(dpo, m)
        return {'DPO': dpo, 'MADPO': madpo}

    @staticmethod
    def VHF(close: pd.Series, n: int = 28) -> Dict[str, pd.Series]:
        """Vertical Horizontal Filter."""
        hcp = _hhv(close, n)
        lcp = _llv(close, n)
        denom = _sum((close - _ref(close, 1)).abs(), n)
        denom = denom.replace(0, np.nan)
        vhf = (hcp - lcp) / denom
        return {'VHF': vhf}

    @staticmethod
    def CHO(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, n1: int = 10, n2: int = 20, m: int = 6) -> Dict[str, pd.Series]:
        """Chaikin Oscillator."""
        mid = _cumsum(volume * (2 * close - high - low) / (high + low))
        cho = (_ma(mid, n1) - _ma(mid, n2)) / 100
        macho = _ma(cho, m)
        return {'CHO': cho, 'MACHO': macho}

    @staticmethod
    def DBCD(close: pd.Series, n: int = 5, m: int = 16, t: int = 76) -> Dict[str, pd.Series]:
        """Difference of Bias Convergence Divergence."""
        ma = _ma(close, n)
        bias = (close - ma) / ma
        dif = bias - _ref(bias, m)
        dbcd = _sma(dif, t, 1)
        mm = _ma(dbcd, 5)
        return {'DBCD': dbcd, 'MM': mm}

    @staticmethod
    def DDI(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 13, n1: int = 26, m: int = 1, m1: int = 5) -> Dict[str, pd.Series]:
        """Directional Divergence Index."""
        ref_h = _ref(high, 1)
        ref_l = _ref(low, 1)
        tr = np.maximum((high - ref_h).abs(), (low - ref_l).abs())
        dmz = np.where(high + low <= ref_h + ref_l, 0, tr)
        dmf = np.where(high + low >= ref_h + ref_l, 0, tr)
        sum_dmz = _sum(pd.Series(dmz, index=close.index), n)
        sum_dmf = _sum(pd.Series(dmf, index=close.index), n)
        denom = (sum_dmz + sum_dmf).replace(0, np.nan)
        diz = sum_dmz / denom
        dif = sum_dmf / denom
        ddi = diz - dif
        addi = _sma(ddi, n1, m)
        ad_line = _ma(addi, m1)
        return {'DDI': ddi, 'ADDI': addi, 'ADL': ad_line}

    @staticmethod
    def JS(close: pd.Series, n: int = 5, m1: int = 5, m2: int = 10, m3: int = 20) -> Dict[str, pd.Series]:
        """Acceleration line."""
        ref_close = _ref(close, n)
        js = (close - ref_close) / (n * ref_close) * 100
        return {
            'JS': js,
            f'MAJ{m1}': _ma(js, m1),
            f'MAJ{m2}': _ma(js, m2),
            f'MAJ{m3}': _ma(js, m3),
        }

    @staticmethod
    def QACD(close: pd.Series, n1: int = 12, n2: int = 26, m: int = 9) -> Dict[str, pd.Series]:
        """Quick MACD."""
        dif = _ema(close, n1) - _ema(close, n2)
        macd = _ema(dif, m)
        ddif = dif - macd
        return {'DIF': dif, 'MACD': macd, 'DDIF': ddif}

    @staticmethod
    def UOS(close: pd.Series, high: pd.Series, low: pd.Series, n1: int = 7, n2: int = 14, n3: int = 28, m: int = 6) -> Dict[str, pd.Series]:
        """Ultimate Oscillator."""
        ref_c = _ref(close, 1)
        th = np.maximum(high, ref_c)
        tl = np.minimum(low, ref_c)
        acc1 = _sum(close - tl, n1) / _sum(th - tl, n1)
        acc2 = _sum(close - tl, n2) / _sum(th - tl, n2)
        acc3 = _sum(close - tl, n3) / _sum(th - tl, n3)
        uos = (acc1 * n2 * n3 + acc2 * n1 * n3 + acc3 * n1 * n2) * 100 / (n1 * n2 + n1 * n3 + n2 * n3)
        mauos = _ema(uos, m)
        return {'UOS': uos, 'MAUOS': mauos}

    # ================================================================
    #  三、能量型 (5 indicators)
    # ================================================================

    @staticmethod
    def CR(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 26, m1: int = 10, m2: int = 20, m3: int = 40, m4: int = 62) -> Dict[str, pd.Series]:
        """CR energy indicator."""
        mid = _ref(high + low, 1) / 2
        zero = close * 0
        up = np.maximum(high - mid, 0)
        down = np.maximum(mid - low, 0)
        down_sum = _sum(pd.Series(down, index=close.index), n).replace(0, np.nan)
        cr = _sum(pd.Series(up, index=close.index), n) / down_sum * 100
        ma1 = _ref(_ma(cr, m1), int(m1 / 2.5 + 1))
        ma2 = _ref(_ma(cr, m2), int(m2 / 2.5 + 1))
        ma3 = _ref(_ma(cr, m3), int(m3 / 2.5 + 1))
        ma4 = _ref(_ma(cr, m4), int(m4 / 2.5 + 1))
        return {'CR': cr, f'MA{m1}': ma1, f'MA{m2}': ma2, f'MA{m3}': ma3, f'MA{m4}': ma4}

    @staticmethod
    def PSY(close: pd.Series, n: int = 12, m: int = 6) -> Dict[str, pd.Series]:
        """Psychological line."""
        cond = close > _ref(close, 1)
        psy = _count(cond, n) / n * 100
        mapsy = _ma(psy, m)
        return {'PSY': psy, 'PSYMA': mapsy}

    @staticmethod
    def MASS(high: pd.Series, low: pd.Series, n1: int = 9, n2: int = 25, m: int = 6) -> Dict[str, pd.Series]:
        """Mass index."""
        hl_ema = _ma(high - low, n1)
        mass = _sum(hl_ema / _ma(hl_ema, n1), n2)
        mamass = _ma(mass, m)
        return {'MASS': mass, 'MAMASS': mamass}

    @staticmethod
    def PCNT(close: pd.Series, m: int = 5) -> Dict[str, pd.Series]:
        """Percent change."""
        ref_close = _ref(close, 1)
        pcnt = (close - ref_close) / close * 100
        mapcnt = _ema(pcnt, m)
        return {'PCNT': pcnt, 'MAPCNT': mapcnt}

    @staticmethod
    def WAD(close: pd.Series, high: pd.Series, low: pd.Series, m: int = 30) -> Dict[str, pd.Series]:
        """Williams Accumulation/Distribution."""
        ref_c = _ref(close, 1)
        mida = close - np.minimum(low, ref_c)
        midb = np.where(close < ref_c, close - np.maximum(ref_c, high), 0)
        wad = _sum(np.where(close > ref_c, mida, midb), 0)
        mawad = _ma(pd.Series(wad, index=close.index), m)
        return {'WAD': pd.Series(wad, index=close.index), 'MAWAD': mawad}

    # ================================================================
    #  四、成交量型 (10 indicators)
    # ================================================================

    @staticmethod
    def OBV(close: pd.Series, volume: pd.Series, m: int = 30) -> Dict[str, pd.Series]:
        """On Balance Volume."""
        ref_close = _ref(close, 1)
        direction = np.sign(close - ref_close).fillna(0)
        obv = _cumsum(pd.Series(direction * volume, index=close.index))
        if len(obv) > 0 and len(volume) > 0:
            obv.iloc[0] = volume.iloc[0]
        maobv = _ma(obv, m)
        return {'OBV': obv, 'MAOBV': maobv}

    @staticmethod
    def VR(close: pd.Series, volume: pd.Series, n: int = 26, m: int = 6) -> Dict[str, pd.Series]:
        """Volume Ratio."""
        ref_close = _ref(close, 1)
        av = _sum(np.where(close > ref_close, volume, 0), n)
        bv = _sum(np.where(close < ref_close, volume, 0), n)
        cv = _sum(np.where(close == ref_close, volume, 0), n)
        vr = (av + cv / 2) / (bv + cv / 2) * 100
        mavr = _ma(pd.Series(vr, index=close.index), m)
        return {'VR': pd.Series(vr, index=close.index), 'MAVR': mavr}

    @staticmethod
    def VOLMA(volume: pd.Series, n1: int = 5, n2: int = 10) -> Dict[str, pd.Series]:
        """Volume MA."""
        return {f'VOLMA{n1}': _ma(volume, n1), f'VOLMA{n2}': _ma(volume, n2)}

    @staticmethod
    def WVAD(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, n: int = 24, m: int = 6) -> Dict[str, pd.Series]:
        """Williams Variable Accumulation Distribution."""
        wvad = _sum((close - open_) / (high - low) * volume, n) / 10000
        mawvad = _ma(wvad, m)
        return {'WVAD': wvad, 'MAWVAD': mawvad}

    @staticmethod
    def VOSC(volume: pd.Series, short: int = 12, long: int = 26) -> Dict[str, pd.Series]:
        """Volume Oscillator."""
        ma_short = _ma(volume, short)
        ma_long = _ma(volume, long)
        vosc = (ma_short - ma_long) / ma_short * 100
        return {'VOSC': vosc}

    @staticmethod
    def VRSI(volume: pd.Series, n1: int = 6, n2: int = 12, n3: int = 24) -> Dict[str, pd.Series]:
        """Volume RSI."""
        lv = _ref(volume, 1)
        diff = volume - lv
        pos_diff = np.maximum(diff, 0)
        abs_diff = diff.abs()
        result = {}
        for n in [n1, n2, n3]:
            result[f'VRSI{n}'] = _sma(pos_diff, n, 1) / _sma(abs_diff, n, 1) * 100
        return result

    @staticmethod
    def VSTD(volume: pd.Series, n: int = 10) -> Dict[str, pd.Series]:
        """Volume Standard Deviation."""
        vstd = _std(volume, n)
        return {'VSTD': vstd}

    @staticmethod
    def AMO(amount: pd.Series, n1: int = 5, n2: int = 10) -> Dict[str, pd.Series]:
        """Amount MA."""
        return {'AMOW': amount / 10000, f'AMO{n1}': _ma(amount / 10000, n1), f'AMO{n2}': _ma(amount / 10000, n2)}

    @staticmethod
    def TAPI(close: pd.Series, amount: pd.Series, n: int = 6) -> Dict[str, pd.Series]:
        """Total Amount Per Index."""
        tapi = amount / close
        matapi = _ma(tapi, n)
        return {'TAPI': tapi, 'MATAPI': matapi}

    # ================================================================
    #  五、均线型 (4 indicators)
    # ================================================================

    @staticmethod
    def MA(close: pd.Series, m1: int = 5, m2: int = 10, m3: int = 20, m4: int = 60, m5: int = 0, m6: int = 0, m7: int = 0, m8: int = 0) -> Dict[str, pd.Series]:
        """Moving Average."""
        result = {}
        for i, m in enumerate([m1, m2, m3, m4, m5, m6, m7, m8], 1):
            if m > 0:
                result[f'MA{m}'] = _ma(close, m)
        return result

    @staticmethod
    def EXPMA(close: pd.Series, n1: int = 12, n2: int = 50) -> Dict[str, pd.Series]:
        """Exponential MA."""
        return {f'EXPMA{n1}': _ema(close, n1), f'EXPMA{n2}': _ema(close, n2)}

    @staticmethod
    def BBI(close: pd.Series, m1: int = 3, m2: int = 6, m3: int = 12, m4: int = 24) -> Dict[str, pd.Series]:
        """Bull and Bear Index."""
        bbi = (_ma(close, m1) + _ma(close, m2) + _ma(close, m3) + _ma(close, m4)) / 4
        return {'BBI': bbi}

    @staticmethod
    def AMV(volume: pd.Series, amount: pd.Series, n1: int = 5, n2: int = 13, n3: int = 34, n4: int = 60) -> Dict[str, pd.Series]:
        """Average Market Value."""
        return {
            f'AMV{n1}': _sum(amount, n1) / _sum(volume, n1),
            f'AMV{n2}': _sum(amount, n2) / _sum(volume, n2),
            f'AMV{n3}': _sum(amount, n3) / _sum(volume, n3),
            f'AMV{n4}': _sum(amount, n4) / _sum(volume, n4),
        }

    # ================================================================
    #  六、路径型 (6 indicators)
    # ================================================================

    @staticmethod
    def BOLL(close: pd.Series, n: int = 20, k: int = 2) -> Dict[str, pd.Series]:
        """Bollinger Bands."""
        mid = _ma(close, n)
        vart1 = (close - mid) ** 2
        vart2 = _ma(vart1, n)
        vart3 = np.sqrt(vart2)
        upper = mid + k * vart3
        lower = mid - k * vart3
        boll = _ref(mid, 1)
        ub = _ref(upper, 1)
        lb = _ref(lower, 1)
        return {'BOLL': boll, 'UB': ub, 'LB': lb}

    @staticmethod
    def ENE(close: pd.Series, n: int = 25, m1: int = 6, m2: int = 6) -> Dict[str, pd.Series]:
        """ENE轨道线."""
        ma = _ma(close, n)
        upper = ma * (1 + m1 / 100)
        lower = ma * (1 - m2 / 100)
        ene = (upper + lower) / 2
        return {'UPPER': upper, 'ENE': ene, 'LOWER': lower}

    @staticmethod
    def MIKE(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 10) -> Dict[str, pd.Series]:
        """Mike indicator."""
        hlc = _ref(_ma((high + low + close) / 3, n), 1)
        hv = _ema(_hhv(high, n), 3)
        lv = _ema(_llv(low, n), 3)
        wr = _ema(hlc * 2 - lv, 3)
        mr = _ema(hlc + hv - lv, 3)
        sr = _ema(2 * hv - lv, 3)
        ws = _ema(hlc * 2 - hv, 3)
        ms = _ema(hlc - hv + lv, 3)
        ss = _ema(2 * lv - hv, 3)
        return {'WEKR': wr, 'MIDR': mr, 'STOR': sr, 'WEKS': ws, 'MIDS': ms, 'STOS': ss}

    @staticmethod
    def PBX(close: pd.Series, m1: int = 4, m2: int = 6, m3: int = 9, m4: int = 13, m5: int = 18, m6: int = 24) -> Dict[str, pd.Series]:
        """Waterfall lines."""
        return {
            f'PBX{m1}': (_ema(close, m1) + _ema(close, m1 * 2) + _ema(close, m1 * 4)) / 3,
            f'PBX{m2}': (_ema(close, m2) + _ema(close, m2 * 2) + _ema(close, m2 * 4)) / 3,
            f'PBX{m3}': (_ema(close, m3) + _ema(close, m3 * 2) + _ema(close, m3 * 4)) / 3,
            f'PBX{m4}': (_ema(close, m4) + _ema(close, m4 * 2) + _ema(close, m4 * 4)) / 3,
            f'PBX{m5}': (_ema(close, m5) + _ema(close, m5 * 2) + _ema(close, m5 * 4)) / 3,
            f'PBX{m6}': (_ema(close, m6) + _ema(close, m6 * 2) + _ema(close, m6 * 4)) / 3,
        }

    @staticmethod
    def XS(close: pd.Series, high: pd.Series, low: pd.Series, volume: pd.Series, n: int = 13) -> Dict[str, pd.Series]:
        """Xue's channel."""
        var2 = close * volume
        p1 = _ema(var2, 3) / _ema(volume, 3)
        p2 = _ema(var2, 6) / _ema(volume, 6)
        p3 = _ema(var2, 12) / _ema(volume, 12)
        p4 = _ema(var2, 24) / _ema(volume, 24)
        var3 = _ema((p1 + p2 + p3 + p4) / 4, n)
        sup = 1.06 * var3
        sdn = var3 * 0.94
        var4 = _ema(close, 9)
        lup = _ema(var4 * 1.14, 5)
        ldn = _ema(var4 * 0.86, 5)
        return {'SUP': sup, 'SDN': sdn, 'LUP': lup, 'LDN': ldn}

    @staticmethod
    def BBIBOLL(close: pd.Series, n: int = 11, m: int = 6) -> Dict[str, pd.Series]:
        """BBI Bollinger."""
        bbi = (_ma(close, 3) + _ma(close, 6) + _ma(close, 12) + _ma(close, 24)) / 4
        std = _std(bbi, n)
        upper = bbi + m * std
        lower = bbi - m * std
        return {'BBIBOLL': bbi, 'UPPER': upper, 'LOWER': lower}

    # ================================================================
    #  七、其他型 (4 indicators)
    # ================================================================

    @staticmethod
    def ASI(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, m1: int = 26, m2: int = 10) -> Dict[str, pd.Series]:
        """Accumulation Swing Index."""
        ref_c = _ref(close, 1)
        ref_o = _ref(open_, 1)
        ref_l = _ref(low, 1)
        aa = (high - ref_c).abs()
        bb = (low - ref_c).abs()
        cc = (high - ref_l).abs()
        dd = (ref_c - ref_o).abs()
        r_a = aa + bb / 2 + dd / 4
        r_b = bb + aa / 2 + dd / 4
        r_c = cc + dd / 4
        r = np.where((aa > bb) & (aa > cc), r_a, np.where((bb > cc) & (bb > aa), r_b, r_c))
        r = pd.Series(r, index=close.index).replace(0, np.nan)
        x = (close - ref_c + (close - open_) / 2 + ref_c - ref_o)
        si = 16 * x / r * np.maximum(aa, bb)
        asi = _sum(si, m1)
        return {'SI': si, 'ASI': asi}

    @staticmethod
    def ATR(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 14) -> Dict[str, pd.Series]:
        """Average True Range."""
        mtr = np.maximum(np.maximum(high - low, (_ref(close, 1) - high).abs()), (_ref(close, 1) - low).abs())
        atr = _ma(pd.Series(mtr, index=close.index), n)
        return {'MTR': pd.Series(mtr, index=close.index), 'ATR': atr}

    @staticmethod
    def SAR(close: pd.Series, high: pd.Series, low: pd.Series, n: int = 4, step: float = 0.02, max_af: float = 0.2) -> Dict[str, pd.Series]:
        """Parabolic SAR (simplified)."""
        # Simplified implementation - full SAR is complex
        # Use rolling high/low based approximation
        sar = _ema(high, n) - (_ema(high, n) - _ema(low, n)) * step
        return {'SAR': sar}

    @staticmethod
    def CDP(close: pd.Series, high: pd.Series, low: pd.Series) -> Dict[str, pd.Series]:
        """Contrarian Day Pivot."""
        ref_h = _ref(high, 1)
        ref_l = _ref(low, 1)
        ref_c = _ref(close, 1)
        cdp = (ref_h + ref_l + ref_c) / 3
        ah = 2 * cdp + ref_h - 2 * ref_l
        nh = 2 * cdp - ref_l
        nl = 2 * cdp - ref_h
        al = 2 * cdp - 2 * ref_h + ref_l
        return {'AH': ah, 'NH': nh, 'CDP': cdp, 'NL': nl, 'AL': al}


# ================================================================
#  Registry
# ================================================================

CATEGORY_MAP = {
    "overbought_oversold": [
        ("KDJ", TechnicalIndicators.KDJ),
        ("RSI", TechnicalIndicators.RSI),
        ("WR", TechnicalIndicators.WR),
        ("CCI", TechnicalIndicators.CCI),
        ("ROC", TechnicalIndicators.ROC),
        ("MTM", TechnicalIndicators.MTM),
        ("BIAS", TechnicalIndicators.BIAS),
        ("SKDJ", TechnicalIndicators.SKDJ),
        ("MFI", TechnicalIndicators.MFI),
        ("OSC", TechnicalIndicators.OSC),
        ("UDL", TechnicalIndicators.UDL),
        ("ACCER", TechnicalIndicators.ACCER),
        ("RCCD", TechnicalIndicators.RCCD),
        ("MARSI", TechnicalIndicators.MARSI),
    ],
    "trend": [
        ("MACD", TechnicalIndicators.MACD),
        ("DMI", TechnicalIndicators.DMI),
        ("DMA", TechnicalIndicators.DMA),
        ("TRIX", TechnicalIndicators.TRIX),
        ("ARBR", TechnicalIndicators.ARBR),
        ("EMV", TechnicalIndicators.EMV),
        ("DPO", TechnicalIndicators.DPO),
        ("VHF", TechnicalIndicators.VHF),
        ("CHO", TechnicalIndicators.CHO),
        ("DBCD", TechnicalIndicators.DBCD),
        ("DDI", TechnicalIndicators.DDI),
        ("JS", TechnicalIndicators.JS),
        ("QACD", TechnicalIndicators.QACD),
        ("UOS", TechnicalIndicators.UOS),
    ],
    "energy": [
        ("CR", TechnicalIndicators.CR),
        ("PSY", TechnicalIndicators.PSY),
        ("MASS", TechnicalIndicators.MASS),
        ("PCNT", TechnicalIndicators.PCNT),
        ("WAD", TechnicalIndicators.WAD),
    ],
    "volume": [
        ("OBV", TechnicalIndicators.OBV),
        ("VR", TechnicalIndicators.VR),
        ("VOLMA", TechnicalIndicators.VOLMA),
        ("WVAD", TechnicalIndicators.WVAD),
        ("VOSC", TechnicalIndicators.VOSC),
        ("VRSI", TechnicalIndicators.VRSI),
        ("VSTD", TechnicalIndicators.VSTD),
        ("AMO", TechnicalIndicators.AMO),
        ("TAPI", TechnicalIndicators.TAPI),
    ],
    "ma": [
        ("MA", TechnicalIndicators.MA),
        ("EXPMA", TechnicalIndicators.EXPMA),
        ("BBI", TechnicalIndicators.BBI),
        ("AMV", TechnicalIndicators.AMV),
    ],
    "path": [
        ("BOLL", TechnicalIndicators.BOLL),
        ("ENE", TechnicalIndicators.ENE),
        ("MIKE", TechnicalIndicators.MIKE),
        ("PBX", TechnicalIndicators.PBX),
        ("XS", TechnicalIndicators.XS),
        ("BBIBOLL", TechnicalIndicators.BBIBOLL),
    ],
    "other": [
        ("ASI", TechnicalIndicators.ASI),
        ("ATR", TechnicalIndicators.ATR),
        ("SAR", TechnicalIndicators.SAR),
        ("CDP", TechnicalIndicators.CDP),
    ],
}

ALL_INDICATORS = []
for cat_list in CATEGORY_MAP.values():
    ALL_INDICATORS.extend(cat_list)


def get_indicator(name: str) -> Optional[Callable]:
    """Get indicator function by name."""
    name = name.upper()
    for ind_name, func in ALL_INDICATORS:
        if ind_name.upper() == name:
            return func
    return None


def get_category(category: str) -> List[Tuple[str, Callable]]:
    """Get all indicators in a category."""
    return CATEGORY_MAP.get(category, [])
