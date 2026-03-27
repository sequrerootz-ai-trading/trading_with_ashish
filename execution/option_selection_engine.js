'use strict';

const MARKET_DEFAULTS = {
  EQUITY: {
    enabledFlag: 'ENABLE_EQUITY',
    hardPremiumMin: 20,
    hardPremiumMax: 300,
    preferredPremiumMin: 30,
    preferredPremiumMax: 150,
    maxIv: 32,
    pullbackMinPct: 0.01,
    pullbackMaxPct: 0.02,
  },
  MCX: {
    enabledFlag: 'ENABLE_MCX',
    hardPremiumMin: 20,
    hardPremiumMax: 400,
    preferredPremiumMin: 50,
    preferredPremiumMax: 300,
    maxIv: 38,
    pullbackMinPct: 0.005,
    pullbackMaxPct: 0.015,
  },
};

function loadConfig(env = process.env) {
  return {
    enableEquity: parseBoolean(env.ENABLE_EQUITY, true),
    enableMcx: parseBoolean(env.ENABLE_MCX, true),
    minPremium: parseNumber(env.MIN_PREMIUM, 50),
    maxPremium: parseNumber(env.MAX_PREMIUM, 250),
    stopLossPercent: parseNumber(env.OPTION_STOP_LOSS_PCT, 0.2),
    riskRewardRatio: parseNumber(env.OPTION_RR_RATIO, 2),
  };
}

function getATMStrike(underlyingPrice, optionChain = []) {
  if (!Number.isFinite(underlyingPrice) || underlyingPrice <= 0) {
    throw new Error('underlyingPrice must be a positive number');
  }

  const strikes = uniqueSortedStrikes(optionChain);
  if (!strikes.length) {
    throw new Error('optionChain must contain at least one strike');
  }

  return strikes.reduce((closest, strike) => {
    if (closest === null) {
      return strike;
    }
    const currentDistance = Math.abs(strike - underlyingPrice);
    const closestDistance = Math.abs(closest - underlyingPrice);
    return currentDistance < closestDistance ? strike : closest;
  }, null);
}

function filterPremium(contract, marketType, config = loadConfig()) {
  const normalizedMarket = normalizeMarketType(marketType);
  const defaults = getMarketDefaults(normalizedMarket);
  const premium = getPremium(contract);

  if (!Number.isFinite(premium) || premium <= 0) {
    return { accepted: false, reason: 'premium_missing', premium };
  }
  if (premium < defaults.hardPremiumMin) {
    return { accepted: false, reason: 'premium_too_cheap', premium };
  }
  if (premium > defaults.hardPremiumMax) {
    return { accepted: false, reason: 'premium_too_expensive', premium };
  }
  if (premium < config.minPremium || premium > config.maxPremium) {
    return { accepted: false, reason: 'premium_outside_config_range', premium };
  }

  const preferred =
    premium >= defaults.preferredPremiumMin && premium <= defaults.preferredPremiumMax;

  return {
    accepted: true,
    reason: preferred ? 'premium_in_preferred_range' : 'premium_acceptable',
    premium,
    preferred,
  };
}

function analyzeOI(contract, optionChain, tradeSignal) {
  const optionType = getDesiredOptionType(tradeSignal);
  const comparable = optionChain.filter((item) => normalizeOptionType(item.optionType) === optionType);
  const oiValues = comparable.map((item) => toNumber(item.oi));
  const volumeValues = comparable.map((item) => toNumber(item.volume));
  const avgOi = average(oiValues);
  const avgVolume = average(volumeValues);
  const contractOi = toNumber(contract.oi);
  const changeInOi = toNumber(contract.changeInOi);
  const priceChange = toNumber(contract.priceChange, 0);
  const volume = toNumber(contract.volume);
  const iv = toNumber(contract.iv);

  let score = 0;
  const reasons = [];
  let rejected = false;

  if (avgOi > 0 && contractOi >= avgOi * 1.6) {
    rejected = true;
    reasons.push(optionType === 'CE' ? 'high_call_oi_resistance' : 'high_put_oi_support');
  } else if (contractOi > avgOi) {
    score += 1;
    reasons.push('healthy_oi_participation');
  }

  if (changeInOi > 0 && priceChange > 0) {
    score += 2;
    reasons.push('oi_buildup_with_price_confirmation');
  } else if (changeInOi > 0) {
    score += 1;
    reasons.push('oi_buildup_present');
  }

  if (avgVolume > 0 && volume >= avgVolume * 1.25) {
    score += 2;
    reasons.push('high_volume_liquidity');
  } else if (volume >= avgVolume * 0.8) {
    score += 1;
    reasons.push('adequate_volume');
  } else {
    reasons.push('thin_volume');
  }

  if (Number.isFinite(iv)) {
    reasons.push(`iv_${formatNumber(iv)}`);
  }

  return {
    rejected,
    score,
    reasons,
    metrics: {
      oi: contractOi,
      averageOi: avgOi,
      changeInOi,
      volume,
      averageVolume: avgVolume,
      iv,
    },
  };
}

function selectBestStrike(params) {
  const {
    signal,
    marketType,
    underlyingPrice,
    optionChain,
    config = loadConfig(),
    marketContext = {},
  } = params;

  const normalizedSignal = normalizeSignal(signal);
  const normalizedMarket = normalizeMarketType(marketType);
  validateMarketEnabled(normalizedMarket, config);

  if (normalizedSignal === 'NO TRADE') {
    return { selected: null, reason: 'signal_is_no_trade' };
  }
  if (marketContext.sideways === true) {
    return { selected: null, reason: 'sideways_market' };
  }

  const atmStrike = getATMStrike(underlyingPrice, optionChain);
  const step = inferStrikeStep(optionChain, atmStrike);
  const desiredOptionType = getDesiredOptionType(normalizedSignal);
  const targetDelta = 0.5;
  const candidateStrikes = buildCandidateStrikes(normalizedSignal, atmStrike, step);
  const defaults = getMarketDefaults(normalizedMarket);
  const ivCap = Number.isFinite(marketContext.maxIv)
    ? marketContext.maxIv
    : defaults.maxIv;

  const candidates = optionChain
    .filter((contract) => normalizeOptionType(contract.optionType) === desiredOptionType)
    .filter((contract) => candidateStrikes.includes(toNumber(contract.strike)));

  if (!candidates.length) {
    return { selected: null, reason: 'no_near_atm_candidates' };
  }

  let best = null;

  for (const contract of candidates) {
    const premiumCheck = filterPremium(contract, normalizedMarket, config);
    if (!premiumCheck.accepted) {
      continue;
    }

    const iv = toNumber(contract.iv);
    if (Number.isFinite(iv) && iv > ivCap) {
      continue;
    }

    const oiAnalysis = analyzeOI(contract, optionChain, normalizedSignal);
    if (oiAnalysis.rejected) {
      continue;
    }

    const strike = toNumber(contract.strike);
    const delta = Math.abs(toNumber(contract.delta, targetDelta));
    const score =
      scoreStrikeDistance(normalizedSignal, strike, atmStrike, step) +
      scoreDelta(delta, targetDelta) +
      oiAnalysis.score +
      scoreBreakoutContext(marketContext);

    const strikeType = classifyStrikeType(normalizedSignal, strike, underlyingPrice, step);
    const reasoning = [
      strikeType,
      premiumCheck.reason,
      ...oiAnalysis.reasons,
    ];

    const ranked = {
      contract,
      strikeType,
      atmStrike,
      score,
      reasoning,
      premium: premiumCheck.premium,
      metrics: oiAnalysis.metrics,
    };

    if (best === null || ranked.score > best.score) {
      best = ranked;
    }
  }

  if (!best) {
    return { selected: null, reason: 'no_contract_passed_filters' };
  }

  return { selected: best, reason: 'best_contract_selected' };
}

function calculateSLTarget(contract, options = {}) {
  const premium = getPremium(contract);
  if (!Number.isFinite(premium) || premium <= 0) {
    throw new Error('premium must be a positive number');
  }

  const stopLossPercent = parseNumber(options.stopLossPercent, 0.2);
  const riskRewardRatio = parseNumber(options.riskRewardRatio, 2);
  const fixedStop = premium * (1 - stopLossPercent);
  const swingLow = getRecentSwingLow(contract.premiumHistory, contract.recentSwingLow);
  const stopLoss = roundPrice(
    Number.isFinite(swingLow) ? Math.max(fixedStop, swingLow) : fixedStop
  );
  const risk = Math.max(premium - stopLoss, premium * 0.05);
  const target = roundPrice(premium + risk * riskRewardRatio);

  return {
    stopLoss,
    target,
    risk,
    reward: roundPrice(target - premium),
  };
}

function generateTrade(params) {
  const {
    marketType,
    signal,
    underlyingPrice,
    optionChain,
    config = loadConfig(),
    marketContext = {},
  } = params;

  const normalizedSignal = normalizeSignal(signal);
  const normalizedMarket = normalizeMarketType(marketType);
  validateMarketEnabled(normalizedMarket, config);

  if (normalizedSignal === 'NO TRADE' || marketContext.sideways === true) {
    return noTrade(normalizedMarket, normalizedSignal, 'Market is sideways or signal is NO TRADE');
  }

  const selection = selectBestStrike({
    signal: normalizedSignal,
    marketType: normalizedMarket,
    underlyingPrice,
    optionChain,
    config,
    marketContext,
  });

  if (!selection.selected) {
    return noTrade(normalizedMarket, normalizedSignal, humanizeReasons([selection.reason]));
  }

  const { contract, strikeType, premium, reasoning, metrics } = selection.selected;
  const pricing = calculateSLTarget(contract, config);
  const entry = calculateEntryRange(contract, marketContext, normalizedMarket);
  const confidence = deriveConfidence(selection.selected.score, marketContext);
  const humanSignal = normalizedSignal;

  return {
    symbol: contract.symbol || buildFallbackSymbol(contract),
    market_type: normalizedMarket,
    signal: humanSignal,
    strike_type: strikeType,
    premium: roundPrice(premium),
    entry: `${formatNumber(entry.min)}-${formatNumber(entry.max)}`,
    stop_loss: pricing.stopLoss,
    target: pricing.target,
    confidence,
    reason: humanizeReasons(reasoning, metrics),
  };
}

function generateTrades(tradeRequests, sharedConfig = loadConfig()) {
  if (!Array.isArray(tradeRequests)) {
    throw new Error('tradeRequests must be an array');
  }

  return tradeRequests.map((request) =>
    generateTrade({
      ...request,
      config: { ...sharedConfig, ...(request.config || {}) },
    })
  );
}

function calculateEntryRange(contract, marketContext = {}, marketType) {
  const premium = getPremium(contract);
  const defaults = getMarketDefaults(marketType);
  const aggressive = marketContext.breakout === true && marketContext.volumeConfirmed === true;

  if (aggressive) {
    return {
      min: roundPrice(premium * 0.995),
      max: roundPrice(premium * 1.01),
    };
  }

  return {
    min: roundPrice(premium * (1 - defaults.pullbackMaxPct)),
    max: roundPrice(premium * (1 - defaults.pullbackMinPct)),
  };
}

function validateMarketEnabled(marketType, config) {
  if (marketType === 'EQUITY' && !config.enableEquity) {
    throw new Error('EQUITY trades are disabled by ENABLE_EQUITY=false');
  }
  if (marketType === 'MCX' && !config.enableMcx) {
    throw new Error('MCX trades are disabled by ENABLE_MCX=false');
  }
}

function normalizeSignal(signal) {
  const value = String(signal || '').trim().toUpperCase();
  if (value === 'BUY_CE' || value === 'BUY CALL') {
    return 'BUY CALL';
  }
  if (value === 'BUY_PE' || value === 'BUY PUT') {
    return 'BUY PUT';
  }
  return 'NO TRADE';
}

function normalizeMarketType(marketType) {
  const value = String(marketType || '').trim().toUpperCase();
  if (!MARKET_DEFAULTS[value]) {
    throw new Error(`Unsupported market type: ${marketType}`);
  }
  return value;
}

function getDesiredOptionType(signal) {
  return signal === 'BUY CALL' ? 'CE' : 'PE';
}

function getMarketDefaults(marketType) {
  return MARKET_DEFAULTS[marketType];
}

function buildCandidateStrikes(signal, atmStrike, step) {
  if (signal === 'BUY CALL') {
    return [atmStrike, atmStrike + step, atmStrike - step];
  }
  return [atmStrike, atmStrike - step, atmStrike + step];
}

function inferStrikeStep(optionChain, atmStrike) {
  const strikes = uniqueSortedStrikes(optionChain);
  if (strikes.length < 2) {
    return 50;
  }

  const positiveDiffs = [];
  for (let index = 1; index < strikes.length; index += 1) {
    const diff = strikes[index] - strikes[index - 1];
    if (diff > 0) {
      positiveDiffs.push(diff);
    }
  }

  if (!positiveDiffs.length) {
    return 50;
  }

  const nearest = positiveDiffs.sort((a, b) => a - b)[0];
  return Number.isFinite(nearest) ? nearest : 50;
}

function scoreStrikeDistance(signal, strike, atmStrike, step) {
  if (strike === atmStrike) {
    return 4;
  }
  if (signal === 'BUY CALL' && strike === atmStrike + step) {
    return 3;
  }
  if (signal === 'BUY PUT' && strike === atmStrike - step) {
    return 3;
  }
  if (signal === 'BUY CALL' && strike === atmStrike - step) {
    return 2;
  }
  if (signal === 'BUY PUT' && strike === atmStrike + step) {
    return 2;
  }
  return 0;
}

function scoreDelta(delta, targetDelta) {
  if (!Number.isFinite(delta)) {
    return 0;
  }
  const distance = Math.abs(delta - targetDelta);
  if (distance <= 0.08) {
    return 3;
  }
  if (distance <= 0.15) {
    return 2;
  }
  if (distance <= 0.22) {
    return 1;
  }
  return 0;
}

function scoreBreakoutContext(marketContext = {}) {
  let score = 0;
  if (marketContext.breakout === true) {
    score += 1;
  }
  if (marketContext.volumeConfirmed === true) {
    score += 1;
  }
  return score;
}

function classifyStrikeType(signal, strike, underlyingPrice, step) {
  if (Math.abs(strike - underlyingPrice) <= step * 0.5) {
    return 'ATM';
  }
  if (signal === 'BUY CALL') {
    return strike < underlyingPrice ? 'ITM' : 'OTM';
  }
  return strike > underlyingPrice ? 'ITM' : 'OTM';
}

function deriveConfidence(score, marketContext = {}) {
  let adjustedScore = score;
  if (marketContext.breakout === true && marketContext.volumeConfirmed === true) {
    adjustedScore += 1;
  }
  if (adjustedScore >= 10) {
    return 'HIGH';
  }
  if (adjustedScore >= 7) {
    return 'MEDIUM';
  }
  return 'LOW';
}

function humanizeReasons(reasons, metrics = {}) {
  const mapping = {
    signal_is_no_trade: 'Signal engine returned NO TRADE',
    sideways_market: 'Market is sideways, so option buying is avoided',
    no_near_atm_candidates: 'No liquid ATM or near-ATM strikes were available',
    no_contract_passed_filters: 'No contract passed premium, IV, and OI filters',
    premium_missing: 'Premium data is missing',
    premium_too_cheap: 'Premium is too cheap and likely lacks quality movement',
    premium_too_expensive: 'Premium is too expensive for clean risk-reward',
    premium_outside_config_range: 'Premium is outside configured buy range',
    premium_in_preferred_range: 'Premium is in the preferred buy zone',
    premium_acceptable: 'Premium is acceptable for entry',
    high_call_oi_resistance: 'High call OI suggests nearby resistance',
    high_put_oi_support: 'High put OI suggests nearby support',
    healthy_oi_participation: 'Open interest participation is healthy',
    oi_buildup_with_price_confirmation: 'OI is building with price confirmation',
    oi_buildup_present: 'Open interest is building',
    high_volume_liquidity: 'Volume is strong and liquidity is healthy',
    adequate_volume: 'Volume is acceptable',
    thin_volume: 'Volume is thin',
    ATM: 'ATM strike offers the best balance of liquidity and delta',
    ITM: 'Slight ITM gives safer delta exposure',
    OTM: 'Near OTM keeps premium efficient without going deep OTM',
  };

  const readable = [];
  for (const reason of reasons) {
    if (!reason) {
      continue;
    }
    if (mapping[reason]) {
      readable.push(mapping[reason]);
      continue;
    }
    if (String(reason).startsWith('iv_')) {
      readable.push(`Implied volatility is ${String(reason).slice(3)}`);
      continue;
    }
    readable.push(String(reason).replace(/_/g, ' '));
  }

  if (Number.isFinite(metrics.oi) && Number.isFinite(metrics.changeInOi)) {
    readable.push(`OI ${formatNumber(metrics.oi)} with change ${formatNumber(metrics.changeInOi)}`);
  }
  if (Number.isFinite(metrics.volume)) {
    readable.push(`Volume ${formatNumber(metrics.volume)}`);
  }

  return readable.join(', ');
}

function noTrade(marketType, signal, reason) {
  return {
    symbol: null,
    market_type: marketType,
    signal: 'NO TRADE',
    strike_type: null,
    premium: null,
    entry: null,
    stop_loss: null,
    target: null,
    confidence: 'LOW',
    reason: signal === 'NO TRADE' ? reason : `${signal}: ${reason}`,
  };
}

function getRecentSwingLow(premiumHistory, fallbackSwingLow) {
  if (Array.isArray(premiumHistory) && premiumHistory.length) {
    const values = premiumHistory.map((item) => toNumber(item)).filter(Number.isFinite);
    if (values.length) {
      return Math.min(...values);
    }
  }
  return toNumber(fallbackSwingLow, Number.NaN);
}

function buildFallbackSymbol(contract) {
  return `${contract.underlying || 'UNDERLYING'} ${formatNumber(contract.strike)} ${normalizeOptionType(contract.optionType)}`;
}

function normalizeOptionType(optionType) {
  const value = String(optionType || '').trim().toUpperCase();
  if (value === 'CALL') {
    return 'CE';
  }
  if (value === 'PUT') {
    return 'PE';
  }
  return value;
}

function getPremium(contract) {
  return toNumber(contract.premium, toNumber(contract.lastPrice));
}

function uniqueSortedStrikes(optionChain) {
  return [...new Set(optionChain.map((item) => toNumber(item.strike)).filter(Number.isFinite))].sort(
    (left, right) => left - right
  );
}

function average(values) {
  const usable = values.filter(Number.isFinite);
  if (!usable.length) {
    return 0;
  }
  return usable.reduce((sum, value) => sum + value, 0) / usable.length;
}

function parseBoolean(value, fallback) {
  if (value === undefined || value === null || value === '') {
    return fallback;
  }
  return ['1', 'true', 'yes', 'on'].includes(String(value).trim().toLowerCase());
}

function parseNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function toNumber(value, fallback = Number.NaN) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function roundPrice(value) {
  return Math.round(value * 100) / 100;
}

function formatNumber(value) {
  return roundPrice(Number(value)).toFixed(2).replace(/\.00$/, '');
}

module.exports = {
  loadConfig,
  getATMStrike,
  filterPremium,
  analyzeOI,
  selectBestStrike,
  calculateSLTarget,
  generateTrade,
  generateTrades,
};
