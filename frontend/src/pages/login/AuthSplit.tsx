import type { ReactNode } from "react";
import PolicyLinks from "../../components/billing/PolicyLinks";
import { useMediaQuery } from "../../components/chat/useMediaQuery";
import HeroArt from "./HeroArt";
import { Deck, Headline, Pitch, Tagline } from "./LoginCopy";
import ProductTour from "./ProductTour";
import "../login.css";

/** Below this the aside (and its tour) isn't rendered: login.css hides it at the same width. */
const PHONE_QUERY = "(max-width: 799.98px)";

/** The brand mark and name (decorative here: the page's heading names the app for assistive tech). */
function Brand() {
  return (
    <span className="ui-brand" aria-hidden="true">
      <span className="ui-brand-mark">N</span>
      <span className="ui-brand-name">AskNuvexusDoc</span>
    </span>
  );
}

/**
 * The front door's split page (visual spec 6.28), shared by sign-in and sign-up: the card (`children`) on the right
 * and, on the left, the aside with the owner's hero picture and copy around the product tour. The card comes first in
 * the DOM, so keyboard and screen-reader users reach it before the copy; the grid places the aside on the left, and
 * the card stays in view while the page scrolls. Phones get no aside: the picture shows above the card (placed there
 * by CSS, it follows the card in the DOM) and the copy below it; `compactOnPhone` (the sent and closed states) leaves
 * the picture out, so the brand alone leads.
 */
export default function AuthSplit({
  children,
  compactOnPhone = false,
}: {
  children: ReactNode;
  compactOnPhone?: boolean;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  return (
    <div className="login-page">
      <main className="login-main">
        <div className="login-card">
          {children}

          {phone ? (
            <>
              <div className="login-mobile-hero">
                <Brand />
                <Headline />
                {compactOnPhone ? null : <HeroArt phone />}
              </div>
              <div className="login-about">
                <Tagline />
                <Deck />
                <Pitch />
              </div>
            </>
          ) : null}

          <p className="login-credit">
            Designed and developed by Elarxsys AI Hub Pvt. Ltd.{" "}
            <span className="login-credit-motto">(Intelligent : Innovation : Intellect)</span>
            <span className="login-copyright">© {new Date().getFullYear()} asknuvexusdoc.com. All rights reserved.</span>
          </p>
          <PolicyLinks compact className="login-policies" />
        </div>
      </main>

      {phone ? null : (
        <aside className="login-aside" aria-label="About AskNuvexusDoc">
          <div className="login-hero">
            <Brand />
            <Headline />
            <Tagline />
            <HeroArt />
            <Deck />
          </div>
          <ProductTour />
          <Pitch />
        </aside>
      )}
    </div>
  );
}
