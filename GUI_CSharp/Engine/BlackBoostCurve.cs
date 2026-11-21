using System;

namespace GUI.Engine
{
    /// <summary>
    /// Black Boost curve. A curve from black to white always has an average slope (contrast) of exactly 1, so the
    /// extra contrast the shadow lift spends near black has to be taken back somewhere above it. This curve is built
    /// from its slope so that pay-back never lands on the midtones (walls, player models, floors).
    ///
    /// The slope starts at 1 + 3.8 * boost near black, eases down smoothly to a constant by x = 0.29 and stays there:
    /// - Classic: constant up to white (0.86x everywhere above 0.29 at boost 35 %, 0.76x at 60 %, 0.59x at 100 %).
    /// - Enhanced: the brightest band (above ~0.85: sky, lamps) pays a larger share (0.7x the slope of the rest), so
    ///   0.29-0.85 keeps 0.90x at 35 % while the sky drops to 0.63x. The step is smoothed over 0.80-0.90.
    /// Shadow output at 35 %: 0.05 -> 0.106, 0.10 -> 0.19.
    ///
    /// Replaced the original lift (gamma + 3.8 * boost * x * (1 - x)^4) on 2026-11-21: same shadows, but it squeezed
    /// 0.3-0.5 down to 0.73x at 35 % and 0.18x at 100 %.
    /// </summary>
    public static class BlackBoostCurve
    {
        private const double KneeX = 0.29;
        private const double ShoulderX = 0.85;
        private const double ShoulderHalfWidth = 0.05;
        private const double ShoulderRatio = 0.70;

        /// <summary>Output 0..1 for input x 0..1 (before contrast), for gamma 1.2..3.0 and boost 0..1.</summary>
        public static double Evaluate(double x, double gamma, double boost, bool enhanced)
        {
            if (x <= 0) return 0.0;
            double yGamma = Math.Pow(x, gamma / 2.20);
            if (boost <= 0) return yGamma;
            return yGamma + SlopeShape(x, boost, enhanced) - x;
        }

        // Integral of the slope profile: s0 easing quadratically to sMid by the knee k, sMid up to the shoulder,
        // then sTop = ratio * sMid (smoothstep-blended) when enhanced. sMid is set so the curve ends exactly at 1.
        private static double SlopeShape(double x, double boost, bool shoulder)
        {
            double s0 = 1.0 + 3.8 * boost;
            double k = KneeX;
            double r = shoulder ? ShoulderRatio : 1.0;
            double xs = ShoulderX;
            double sMid = (1.0 - s0 * k / 3.0) / (2.0 * k / 3.0 + (xs - k) + r * (1.0 - xs));
            double sTop = r * sMid;

            if (x < k)
            {
                double u = 1.0 - x / k;
                return sMid * x + (s0 - sMid) * k / 3.0 * (1.0 - u * u * u);
            }

            double yKnee = sMid * k + (s0 - sMid) * k / 3.0;
            if (!shoulder) return yKnee + sMid * (x - k);

            double w = ShoulderHalfWidth;
            double a = xs - w, b = xs + w;
            if (x <= a) return yKnee + sMid * (x - k);

            double ya = yKnee + sMid * (a - k);
            if (x <= b)
            {
                double t = (x - a) / (2.0 * w);
                return ya + sMid * (x - a) + (sTop - sMid) * 2.0 * w * (t * t * t - t * t * t * t / 2.0);
            }

            double yb = ya + sMid * 2.0 * w + (sTop - sMid) * w;
            return yb + sTop * (x - b);
        }
    }
}
