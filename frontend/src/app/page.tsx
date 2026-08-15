import { redirect } from "next/navigation";

export default function RootPage() {
  // The app shell decides where an unauthenticated visitor goes; this is just the entry point.
  redirect("/dashboard");
}
