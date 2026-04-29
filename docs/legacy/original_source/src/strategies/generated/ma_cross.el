[IntrabarOrderGeneration = false]
// AISMART Strategy: MA_Cross
// Simple dual moving average crossover baseline
// Timeframe: 1min
inputs:
    FastLen(12),
    SlowLen(26);

variables:
    FastMA(0),
    SlowMA(0);

FastMA = XAverage(Close, FastLen);
SlowMA = XAverage(Close, SlowLen);

if FastMA crosses above SlowMA then
    Buy("LE") next bar at market;

if FastMA crosses below SlowMA then
    Sell Short("SE") next bar at market;
