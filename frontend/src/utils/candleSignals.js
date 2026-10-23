function getPatternContext(stock) {
  const rows = (stock?.historical?.nse_data || [])
    .map((row) => ({
      date: String(row?.Date || "").slice(0, 10),
      low: Number(row?.LowPrice),
      close: Number(row?.ClosePrice),
      volume: Number(row?.TotalTradedQuantity),
    }))
    .filter((row) => /^\d{4}-\d{2}-\d{2}$/.test(row.date)
      && Number.isFinite(row.low) && row.low > 0
      && Number.isFinite(row.close) && row.close > 0)
    .sort((a, b) => a.date.localeCompare(b.date));

  const today = new Date();
  const todayKey = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const sessions = rows.filter((row) => row.date < todayKey).slice(-60);
  if (sessions.length < 20) {
    return { atSupport: false, upwardConfirmed: false, pullbackVolumeDryUp: false };
  }

  const swingLows = [];
  for (let index = 1; index < sessions.length - 1; index += 1) {
    if (
      sessions[index].low <= sessions[index - 1].low
      && sessions[index].low <= sessions[index + 1].low
    ) {
      swingLows.push({ low: sessions[index].low, index });
    }
  }

  const ltp = Number(stock?.ltp);
  let supportZone = null;
  if (Number.isFinite(ltp) && ltp > 0) {
    for (const anchor of swingLows) {
      const touches = swingLows.filter((point) =>
        Math.abs(point.low - anchor.low) / anchor.low <= 0.015
        && Math.abs(point.index - anchor.index) >= 3
      );
      if (touches.length < 2) continue;
      const level = touches.reduce((sum, point) => sum + point.low, 0) / touches.length;
      const distancePct = ((ltp - level) / level) * 100;
      if (distancePct < 0 || distancePct > 1.5) continue;
      if (!supportZone || distancePct < supportZone.distancePct) {
        supportZone = { level, distancePct, touches: touches.length };
      }
    }
  }

  const candles = (stock?.today_candles_15m || []).filter((candle) => candle?.completed);
  const latest = candles[candles.length - 1];
  const previous = candles[candles.length - 2];
  const upwardConfirmed = Boolean(
    latest && previous
    && Number(latest.close) > Number(latest.open)
    && Number(latest.close) > Number(previous.high)
    && ltp >= Number(latest.close)
  );

  const recent = sessions.slice(-4);
  const prior = sessions.slice(-14, -4);
  const decliningSteps = recent.slice(1).filter(
    (row, index) => row.close < recent[index].close
  ).length;
  const recentVolume = recent.slice(-3)
    .map((row) => row.volume)
    .filter((volume) => Number.isFinite(volume) && volume > 0);
  const priorVolume = prior
    .map((row) => row.volume)
    .filter((volume) => Number.isFinite(volume) && volume > 0);
  const averageRecentVolume = recentVolume.length
    ? recentVolume.reduce((sum, volume) => sum + volume, 0) / recentVolume.length
    : 0;
  const averagePriorVolume = priorVolume.length
    ? priorVolume.reduce((sum, volume) => sum + volume, 0) / priorVolume.length
    : 0;
  const pullbackVolumeDryUp = Boolean(
    recent.length === 4
    && recent[recent.length - 1].close < recent[0].close
    && decliningSteps >= 2
    && averagePriorVolume > 0
    && averageRecentVolume <= averagePriorVolume * 0.8
  );

  return {
    atSupport: Boolean(supportZone),
    upwardConfirmed,
    pullbackVolumeDryUp,
    supportLevel: supportZone?.level,
    supportDistancePct: supportZone?.distancePct,
    supportTouches: supportZone?.touches || 0,
  };
}

function addPatternContext(signal, stock) {
  if (!signal) return null;
  const context = getPatternContext(stock);
  const supported = context.atSupport && context.upwardConfirmed;
  return {
    ...signal,
    context,
    supported,
    // Dry-up is an additional ranking factor, not a requirement for the + marker.
    contextRank: (supported ? 1 : 0) + (context.pullbackVolumeDryUp ? 1 : 0),
  };
}

export function getVolumeSurgeSignal(stock) {
  const candles = Array.isArray(stock?.today_candles_15m)
    ? stock.today_candles_15m
    : [];
  const currentIndex = candles.findLastIndex
    ? candles.findLastIndex((candle) => !candle?.completed)
    : candles.map((candle) => !candle?.completed).lastIndexOf(true);
  if (currentIndex < 1) return null;

  const current = candles[currentIndex];
  let previousCompleted = null;
  for (let index = currentIndex - 1; index >= 0; index -= 1) {
    if (candles[index]?.completed) {
      previousCompleted = candles[index];
      break;
    }
  }
  if (!previousCompleted) return null;

  const currentVolume = Number(current.volume);
  const previousVolume = Number(previousCompleted.volume);
  const open = Number(current.open);
  const close = Number(current.close);
  const priceRising = stock?.metrics?.price_rising_60s === true;
  if (
    ![currentVolume, previousVolume, open, close].every(Number.isFinite)
    || previousVolume <= 0
    || currentVolume <= previousVolume * 2
    || close <= open
    || !priceRising
  ) {
    return null;
  }

  return {
    volumeRatio: currentVolume / previousVolume,
    currentVolume,
    previousVolume,
    currentCandle: current,
  };
}

export function getDojiSignal(stock) {
  const candles = Array.isArray(stock?.today_candles_15m)
    ? stock.today_candles_15m
    : [];
  if (candles.length < 2) return null;

  const doji = candles[candles.length - 2];
  const confirmation = candles[candles.length - 1];
  if (!doji?.completed) return null;

  const open = Number(doji.open);
  const high = Number(doji.high);
  const low = Number(doji.low);
  const close = Number(doji.close);
  const support = Number(stock?.today_support_resistance?.support);
  if (![open, high, low, close, support].every(Number.isFinite) || support <= 0) {
    return null;
  }

  const range = high - low;
  if (range <= 0) return null;
  const bodyToRange = Math.abs(close - open) / range;
  if (bodyToRange > 0.1) return null;

  // Accept a Doji whose range reaches the support zone, including a candle
  // sitting just below support, within the same 0.4% tolerance used by ST.
  const nearSupport = low <= support * 1.004 && high >= support * 0.996;
  const nextCandleTurnedGreen = (
    Number.isFinite(Number(confirmation.open))
    && Number.isFinite(Number(confirmation.close))
    && Number(confirmation.close) > Number(confirmation.open)
  );
  if (!nearSupport || !nextCandleTurnedGreen) return null;

  return {
    support,
    bodyToRange,
    confirmationCompleted: Boolean(confirmation.completed),
    dojiCandle: doji,
    confirmationCandle: confirmation,
  };
}

export function getHammerSignal(stock) {
  const completedCandles = (stock?.today_candles_15m || []).filter(
    (candle) => candle?.completed
  );
  const candle = completedCandles[completedCandles.length - 1];
  if (!candle || completedCandles.length < 5) return null;

  // A hammer is a lower-wick reversal shape after a decline, so require a
  // recent downward drift across the four completed candles before it.
  const priorCloses = completedCandles
    .slice(-5, -1)
    .map((priorCandle) => Number(priorCandle.close));
  if (!priorCloses.every(Number.isFinite)) return null;
  const decliningBars = priorCloses.slice(1).filter(
    (close, index) => close < priorCloses[index]
  ).length;
  if (priorCloses[priorCloses.length - 1] >= priorCloses[0] || decliningBars < 2) {
    return null;
  }

  const open = Number(candle.open);
  const high = Number(candle.high);
  const low = Number(candle.low);
  const close = Number(candle.close);
  if (![open, high, low, close].every(Number.isFinite) || low <= 0 || open <= 0) {
    return null;
  }

  const body = Math.abs(close - open);
  // A zero-size body has no stable wick-to-body ratio, so don't classify it.
  if (body <= 0) return null;

  const lowerWick = Math.min(open, close) - low;
  const upperWick = high - Math.max(open, close);
  if (lowerWick < 2 * body || upperWick > 0.25 * body) return null;

  return addPatternContext({
    color: close >= open ? "green" : "red",
    lowerWickToBody: lowerWick / body,
    upperWickToBody: upperWick / body,
    priorDeclinePct: (priorCloses[0] - priorCloses[priorCloses.length - 1])
      / priorCloses[0] * 100,
    candle,
  }, stock);
}

export function getMorningStarSignal(stock) {
  const completedCandles = (stock?.today_candles_15m || []).filter(
    (candle) => candle?.completed
  );
  // Compare the first pattern candle with five bars before it to avoid
  // treating an ordinary small red candle as a meaningful decline.
  if (completedCandles.length < 8) return null;

  const patternStart = completedCandles.length - 3;
  const [first, middle, third] = completedCandles.slice(patternStart);
  const patternValues = [first, middle, third].flatMap((candle) => [
    Number(candle.open),
    Number(candle.high),
    Number(candle.low),
    Number(candle.close),
  ]);
  if (!patternValues.every(Number.isFinite)) return null;

  const earlierBodies = completedCandles
    .slice(patternStart - 5, patternStart)
    .map((candle) => Math.abs(Number(candle.close) - Number(candle.open)))
    .filter((body) => Number.isFinite(body));
  if (earlierBodies.length < 5) return null;

  const firstBody = Number(first.open) - Number(first.close);
  const middleBody = Math.abs(Number(middle.close) - Number(middle.open));
  const thirdBody = Number(third.close) - Number(third.open);
  const averagePriorBody = earlierBodies.reduce((sum, body) => sum + body, 0)
    / earlierBodies.length;
  if (averagePriorBody <= 0 || firstBody <= 0 || thirdBody <= 0) return null;

  const firstBodyMidpoint = (Number(first.open) + Number(first.close)) / 2;
  if (
    firstBody < 1.5 * averagePriorBody
    || middleBody > 0.5 * firstBody
    || thirdBody < 0.5 * firstBody
    || Number(third.close) <= firstBodyMidpoint
  ) {
    return null;
  }

  return addPatternContext({
    firstBodyToPriorAverage: firstBody / averagePriorBody,
    thirdBodyToFirstBody: thirdBody / firstBody,
    closeAboveFirstMidpoint: Number(third.close) - firstBodyMidpoint,
  }, stock);
}

export function getEngulfingSignal(stock) {
  const completedCandles = (stock?.today_candles_15m || []).filter(
    (candle) => candle?.completed
  );
  if (completedCandles.length < 2) return null;

  const [previous, current] = completedCandles.slice(-2);
  const values = [previous, current].flatMap((candle) => [
    Number(candle.open),
    Number(candle.high),
    Number(candle.low),
    Number(candle.close),
  ]);
  if (!values.every(Number.isFinite)) return null;

  const previousOpen = Number(previous.open);
  const previousClose = Number(previous.close);
  const currentOpen = Number(current.open);
  const currentClose = Number(current.close);
  const previousBody = Math.abs(previousClose - previousOpen);
  const currentBody = Math.abs(currentClose - currentOpen);
  if (previousBody <= 0 || currentBody <= previousBody) return null;

  const bullish = (
    previousClose < previousOpen
    && currentClose > currentOpen
    && currentOpen <= previousClose
    && currentClose >= previousOpen
  );
  if (!bullish) return null;

  return addPatternContext({
    direction: "bullish",
    color: "green",
    bodyToPrevious: currentBody / previousBody,
  }, stock);
}

export function getBreakoutRetestSignal(stock) {
  const candles = (stock?.today_candles_15m || []).filter(
    (candle) => candle?.completed
  );
  if (candles.length < 11) return null;

  const lastIndex = candles.length - 1;
  const latest = candles[lastIndex];
  const previous = candles[lastIndex - 1];
  const latestOpen = Number(latest.open);
  const latestHigh = Number(latest.high);
  const latestClose = Number(latest.close);
  const previousHigh = Number(previous.high);
  if (
    ![latestOpen, latestHigh, latestClose, previousHigh].every(Number.isFinite)
    || latestClose <= latestOpen
    || latestClose <= previousHigh
  ) {
    return null;
  }

  // Keep the setup recent: the breakout must have occurred within the last
  // ten completed bars, and the latest bar must be the post-retest turn-up.
  const firstCandidate = Math.max(8, lastIndex - 9);
  for (let breakoutIndex = lastIndex - 2; breakoutIndex >= firstCandidate; breakoutIndex -= 1) {
    const breakout = candles[breakoutIndex];
    const breakoutClose = Number(breakout.close);
    const breakoutVolume = Number(breakout.volume);
    const prior = candles.slice(breakoutIndex - 8, breakoutIndex);
    const priorResistance = Math.max(...prior.map((candle) => Number(candle.high)));
    const priorVolumes = prior.slice(-5)
      .map((candle) => Number(candle.volume))
      .filter((volume) => Number.isFinite(volume) && volume > 0);
    const averageVolume = priorVolumes.length
      ? priorVolumes.reduce((sum, volume) => sum + volume, 0) / priorVolumes.length
      : 0;
    const volumeRatio = averageVolume > 0 ? breakoutVolume / averageVolume : 0;

    if (
      !Number.isFinite(breakoutClose)
      || !Number.isFinite(priorResistance)
      || !Number.isFinite(breakoutVolume)
      || breakoutClose <= priorResistance
      || volumeRatio < 1.2
    ) {
      continue;
    }

    for (let retestIndex = breakoutIndex + 1; retestIndex < lastIndex; retestIndex += 1) {
      const retest = candles[retestIndex];
      const retestLow = Number(retest.low);
      const retestClose = Number(retest.close);
      const touchedLevel = (
        retestLow >= priorResistance * 0.996
        && retestLow <= priorResistance * 1.004
      );
      const heldLevel = retestClose >= priorResistance;
      const stayedAboveLevel = candles
        .slice(breakoutIndex, lastIndex + 1)
        .every((candle) => Number(candle.close) >= priorResistance);

      if (touchedLevel && heldLevel && stayedAboveLevel) {
        return {
          level: priorResistance,
          breakoutVolumeRatio: volumeRatio,
          barsSinceBreakout: lastIndex - breakoutIndex,
        };
      }
    }
  }

  return null;
}
