import { redirect } from "next/navigation";

/** The prototype opens on the sign-in screen. */
export default function RootPage() {
  redirect("/login");
}
