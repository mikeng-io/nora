"use client";

import { useEffect } from "react";
import { forgetDevice } from "@/lib/device-data";

/** The login page shows only when nobody is signed in on this device (a
 * signed-in visitor is sent on before it renders), so whatever the device
 * still holds belongs to someone who has left: an expired session, a
 * refused account, a sign-out that did not finish. Forget it. */
export function ForgetDevice() {
  useEffect(() => {
    void forgetDevice();
  }, []);
  return null;
}
