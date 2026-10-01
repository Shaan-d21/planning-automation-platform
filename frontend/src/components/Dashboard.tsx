import { useState, type FormEvent } from "react";

import type { BootstrapResponse } from "../api/types";
import { Icon } from "./Icon";

interface DashboardProps {
  bootstrap: BootstrapResponse;
  onStartAssistant: (prompt?: string) => void;
}

const examplePrompts = [
  "Load September actuals",
  "Run sales forecast",
  "Import Product metadata",
  "Start month-end close",
  "Why did this data load fail?"
] as const;

const journeyStages = [
  { number: "01", label: "Understand", icon: "assistant", example: "Load September actuals", detail: "The Assistant identifies the supported Planning outcome and relevant live context." },
  { number: "02", label: "Clarify", icon: "search", example: "Which entity should I use?", detail: "It requests only the missing information needed to prepare the right action." },
  { number: "03", label: "Complete", icon: "check", example: "Data load completed successfully.", detail: "You review important actions, then receive a monitored result and clear evidence." }
] as const;

const capabilities = [
  { icon: "data", visual: "hierarchy", title: "Data & Metadata", description: "Load Planning data and maintain governed metadata using configured Oracle jobs and files.", examples: ["Data loads", "Metadata imports", "Data integrations"] },
  { icon: "automation", visual: "rules", title: "Rules & Planning Processes", description: "Prepare calculations, forecast seeding, allocations, and deployed business rules.", examples: ["Business Rules", "Forecast seeding", "Allocations"] },
  { icon: "calendar", visual: "pipeline", title: "Pipelines & Automation", description: "Coordinate multi-stage Oracle Pipelines and schedule approved recurring work.", examples: ["Multi-step processes", "Recurring workflows", "Planning jobs"] },
  { icon: "activity", visual: "monitoring", title: "Monitoring & Results", description: "Follow execution status, inspect evidence, and understand failures without hunting through menus.", examples: ["Execution status", "Run evidence", "Failure guidance"] }
] as const;

const naturalLanguageExamples = [
  { category: "Load", prompt: "Load September actuals.", more: ["Import Product metadata.", "Load this file into Financials.", "Load last month's actual data."] },
  { category: "Calculate", prompt: "Run the workforce calculation.", more: ["Seed the sales forecast.", "Calculate travel expense.", "Run the allocation rule."] },
  { category: "Automate", prompt: "Run the month-end pipeline.", more: ["Start the forecast pipeline for FY27.", "Schedule the nightly integration.", "Push plan data to reporting."] },
  { category: "Monitor", prompt: "What's the status of my data load?", more: ["Show my latest Pipeline run.", "Did the metadata import finish?", "Which jobs are still running?"] },
  { category: "Troubleshoot", prompt: "Why did the last metadata import fail?", more: ["Explain this Pipeline error.", "Why did the data integration stop?", "What should I correct before retrying?"] }
] as const;

export function Dashboard({ bootstrap, onStartAssistant }: DashboardProps) {
  const [prompt, setPrompt] = useState("");
  const [expandedExample, setExpandedExample] = useState<string | null>("Load");
  const user = bootstrap.user!;
  const assistantAvailable = bootstrap.navigation.some((item) => item.code === "assistant");
  const myWorkAvailable = bootstrap.navigation.some((item) => item.code === "tasks");

  function submitPrompt(event: FormEvent) {
    event.preventDefault();
    const request = prompt.trim();
    if (request && assistantAvailable) onStartAssistant(request);
  }

  function choosePrompt(value: string) {
    setPrompt(value);
    if (assistantAvailable) onStartAssistant(value);
  }

  return (
    <div className="home-page">
      <section className="home-hero" aria-labelledby="home-title">
        <div className="home-hero__content">
          <div className="home-context">
            <span className="home-context__mark"><Icon name="sparkle" /></span>
            <span>{greeting()}, {firstName(user.display_name)}</span>
            {bootstrap.environment?.application_name && <><i aria-hidden="true" /><span>{bootstrap.environment.application_name}</span></>}
          </div>
          <h1 id="home-title">Planning work, made <span>simpler with AI.</span></h1>
          <p>Describe what you need to accomplish in Oracle Planning. The Assistant understands the task, asks for missing details, prepares supported work, and brings the result back to you.</p>
          <div className="home-hero__actions">
            {assistantAvailable && <button className="button button--primary home-primary-action" type="button" onClick={() => onStartAssistant()}><Icon name="assistant" /> Start with AI Assistant <Icon name="arrow" /></button>}
            {myWorkAvailable && <a className="button button--quiet" href="#tasks">Enter platform <Icon name="arrow" /></a>}
          </div>
          <div className="home-trust-note"><Icon name="check" /><span><strong>You remain in control.</strong> Review important actions before execution and follow the result.</span></div>
        </div>

        <AssistantIllustration />

        {assistantAvailable ? (
          <form className="home-assistant-entry" onSubmit={submitPrompt} aria-label="Start an AI Assistant request">
            <div className="home-assistant-entry__heading">
              <span><Icon name="assistant" /></span>
              <div><strong>Ask the AI Assistant</strong><small>Start with the outcome—not the menu or service.</small></div>
            </div>
            <div className="home-prompt-box">
              <label htmlFor="home-assistant-prompt">What would you like to accomplish in Planning?</label>
              <div><input id="home-assistant-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="For example, run the Sales Forecast pipeline for FY27" /><button type="submit" aria-label="Continue this request in AI Assistant" disabled={!prompt.trim()}><Icon name="arrow" /></button></div>
            </div>
            <div className="home-prompt-examples" aria-label="Example requests"><span>Try asking</span><div>{examplePrompts.map((item) => <button type="button" key={item} onClick={() => choosePrompt(item)}>{item}</button>)}</div></div>
          </form>
        ) : (
          <div className="home-assistant-unavailable"><Icon name="assistant" /><div><strong>Assistant access is not assigned to this role</strong><p>You can still use the Planning workspaces available in the platform.</p></div></div>
        )}
      </section>

      <section className="home-section home-language" aria-labelledby="language-title">
        <SectionHeading eyebrow="Ask in your own words" title="Start with everyday Planning work" id="language-title" description="The same supported capability can be described in different ways. Choose an example to see more, or send it directly to the Assistant." />
        <div className="home-language__list">
          {naturalLanguageExamples.map((example) => {
            const expanded = expandedExample === example.category;
            return <article className={`home-language-card${expanded ? " is-expanded" : ""}`} key={example.category}>
              <button className="home-language-card__toggle" type="button" aria-label={`${example.category}: ${example.prompt}`} aria-expanded={expanded} aria-controls={`language-${example.category}`} onClick={() => setExpandedExample(expanded ? null : example.category)}><span>{example.category}</span><strong>{example.prompt}</strong><Icon name="chevron" /></button>
              <div className="home-language-card__details" id={`language-${example.category}`} hidden={!expanded}>
                <div>{example.more.map((item) => <button type="button" disabled={!assistantAvailable} onClick={() => choosePrompt(item)} key={item}>{item}<Icon name="arrow" /></button>)}</div>
                <button className="home-language-card__ask" type="button" disabled={!assistantAvailable} onClick={() => choosePrompt(example.prompt)}>Ask this <Icon name="assistant" /></button>
              </div>
            </article>;
          })}
        </div>
      </section>

      <section className="home-section home-ai-explainer" aria-labelledby="ai-explainer-title">
        <SectionHeading eyebrow="How the Assistant works" title="One guided journey from request to result" id="ai-explainer-title" description="The Assistant keeps the process focused: understand the outcome, clarify only what is missing, then complete the supported path with you in control." />
        <div className="home-journey" role="list">{journeyStages.map((stage) => <JourneyStage {...stage} key={stage.number} />)}</div>
      </section>

      <section className="home-section home-capabilities" aria-labelledby="capabilities-title">
        <SectionHeading eyebrow="Planning capabilities" title="One intelligent entry point for common Planning work" id="capabilities-title" description="The Assistant connects natural requests to supported, governed capabilities in the platform and the connected Oracle environment." />
        <div className="home-capability-grid">
          {capabilities.map((capability) => <article className="home-capability-card" key={capability.title}><header><span className="home-capability-card__icon"><Icon name={capability.icon} /></span><CapabilityVisual kind={capability.visual} /></header><h3>{capability.title}</h3><p>{capability.description}</p><ul>{capability.examples.map((example) => <li key={example}><Icon name="check" />{example}</li>)}</ul></article>)}
        </div>
      </section>

      <section className="home-section home-comparison" aria-labelledby="comparison-title">
        <SectionHeading eyebrow="A simpler way to work" title="Describe the outcome instead of finding the path" id="comparison-title" description="Oracle controls remain in place. The Assistant reduces the navigation and preparation effort required to reach them." />
        <div className="home-comparison__grid"><WorkflowPath tone="manual" label="Traditional navigation" title="Several decisions before work begins" steps={["Navigate", "Find screen", "Locate object", "Configure parameters", "Run", "Monitor result"]} /><WorkflowPath tone="assistant" label="With AI Assistant" title="“Run the Sales Forecast pipeline for FY27.”" steps={["Understand", "Clarify if needed", "Review", "Execute", "Return result"]} /></div>
      </section>

      <section className="home-section home-control" aria-labelledby="control-title">
        <div className="home-control__copy"><SectionHeading eyebrow="Governed by design" title="AI assists. You remain in control." id="control-title" description="The Assistant can gather context and prepare a supported operation, while important actions remain visible for your review before execution." /><ol className="home-control__steps">{["Assistant prepares", "Review", "Confirm", "Execute", "See result"].map((step, index) => <li key={step}><span>{index + 1}</span>{step}</li>)}</ol></div>
        <article className="home-approval-preview" aria-label="Illustrative approval review"><header><span><Icon name="check" /></span><div><small>Ready for your review</small><h3>Run Sales Forecast Pipeline?</h3></div><b>Elevated</b></header><dl><div><dt>Environment</dt><dd>{bootstrap.environment?.application_name || "Planning"}</dd></div><div><dt>Period</dt><dd>FY27</dd></div><div><dt>Pipeline</dt><dd>Sales_Forecast</dd></div></dl><footer><span>Cancel</span><strong>Confirm and run <Icon name="arrow" /></strong></footer><p><Icon name="check" /> Illustrative review—nothing runs from this card.</p></article>
      </section>

      <section className="home-final-cta" aria-labelledby="final-cta-title"><div><span className="eyebrow">Your next Planning task</span><h2 id="final-cta-title">Ready to simplify your Planning work?</h2><p>Describe what you need and let the Assistant guide the supported workflow.</p></div><div>{assistantAvailable && <button className="button button--primary" type="button" onClick={() => onStartAssistant()}><Icon name="assistant" /> Ask AI Assistant</button>}{myWorkAvailable && <a className="button button--quiet" href="#tasks">Explore platform <Icon name="arrow" /></a>}</div></section>
    </div>
  );
}

function AssistantIllustration() {
  return <div className="home-assistant-illustration" role="img" aria-label="The AI Assistant turns a Planning request into a reviewed, monitored action"><div className="home-ai-orbit home-ai-orbit--one" /><div className="home-ai-orbit home-ai-orbit--two" /><article className="home-ai-request"><span>You</span><p>Run the Sales Forecast pipeline for FY27.</p></article><div className="home-ai-core"><span className="home-ai-character" aria-hidden="true"><i className="home-ai-character__antenna" /><span className="home-ai-character__face"><i /><i /><b /></span></span><strong>Planning Assistant</strong><small>Understanding request…</small><span className="home-ai-thinking" aria-hidden="true"><i /><i /><i /></span></div><div className="home-ai-workflow" aria-hidden="true"><span><i>1</i>Pipeline identified</span><span><i>2</i>Parameters validated</span><span><i><Icon name="check" /></i>Ready for your review</span></div><span className="home-ai-signal home-ai-signal--data"><Icon name="data" /></span><span className="home-ai-signal home-ai-signal--automation"><Icon name="automation" /></span></div>;
}

function JourneyStage({ number, label, icon, example, detail }: { number: string; label: string; icon: "assistant" | "search" | "check"; example: string; detail: string }) {
  return <article className="home-journey__stage" role="listitem"><header><span>{number}</span><i><Icon name={icon} /></i></header><small>{label}</small><h3>“{example}”</h3><p>{detail}</p></article>;
}

function CapabilityVisual({ kind }: { kind: "hierarchy" | "rules" | "pipeline" | "monitoring" }) {
  if (kind === "hierarchy") return <span className="home-mini-visual home-mini-visual--hierarchy" aria-hidden="true"><i /><i /><i /><i /></span>;
  if (kind === "rules") return <span className="home-mini-visual home-mini-visual--rules" aria-hidden="true"><i /><b>+</b><i /><b>=</b><i /></span>;
  if (kind === "pipeline") return <span className="home-mini-visual home-mini-visual--pipeline" aria-hidden="true"><i /><i /><i /></span>;
  return <span className="home-mini-visual home-mini-visual--monitoring" aria-hidden="true"><i /><i /><i /></span>;
}

function WorkflowPath({ tone, label, title, steps }: { tone: "manual" | "assistant"; label: string; title: string; steps: string[] }) {
  return <article className={`home-workflow-path home-workflow-path--${tone}`}><header>{tone === "assistant" && <span><Icon name="assistant" /></span>}<div><small>{label}</small><h3>{title}</h3></div></header><ol>{steps.map((step, index) => <li key={step}><span>{tone === "assistant" && index === steps.length - 1 ? <Icon name="check" /> : index + 1}</span><strong>{step}</strong></li>)}</ol></article>;
}

function SectionHeading({ eyebrow, title, description, id }: { eyebrow: string; title: string; description: string; id: string }) {
  return <header className="home-section-heading"><span className="eyebrow">{eyebrow}</span><h2 id={id}>{title}</h2><p>{description}</p></header>;
}

function greeting() { const hour = new Date().getHours(); return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"; }
function firstName(name: string) { return name.trim().split(/\s+/)[0] || name; }
