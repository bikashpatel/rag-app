/** Leaves the app in this tab for Razorpay's page (a top-level navigation, which the CSP doesn't restrict). */
export function leaveFor(url: string): void {
  window.location.assign(url);
}
