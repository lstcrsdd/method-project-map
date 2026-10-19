import React from "react";
import { InvoiceService, Repo } from "./billing/invoice";

const repo: Repo = { save: () => undefined };

export function App() {
  const service = new InvoiceService(repo);
  return <Summary service={service} />;
}

function Summary(props: { service: InvoiceService }) {
  return <div>{String(props.service)}</div>;
}
