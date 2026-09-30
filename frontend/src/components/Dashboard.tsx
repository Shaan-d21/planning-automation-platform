import { useState, type FormEvent } from "react";

import type { BootstrapResponse, HomeResponse } from "../api/types";
import { Icon } from "./Icon";

interface DashboardProps {
  bootstrap: BootstrapResponse;
  home: HomeResponse;
  onStartAssistant: (prompt?: string) => void;
}

const examplePrompts = [
  "Load this month's actuals",
  "Run forecast seeding",
  "Import Product metadata",
  "Start month-end close",
  "Why did the last data load fail?"
] as const;

const capabilities = [
  {
    icon: "data",
    title: "Data & metadata",
    description: "Load Planning data, import metadata, and work with configured integrations and files.",
    examples: ["Data loads", "Metadata imports", "Data integrations"]
  },
  {
    icon: "automation",
    title: "Rules & Planning processes",
    description: "Prepare calculations, forecast seeding, allocations, and other governed Planning operations.",
    examples: ["Business Rules", "Forecast seeding", "Allocations"]
  },
  {
    icon: "calendar",
    title: "Pipelines & automation",
    description: "Run coordinated Oracle Pipelines and schedule approved recurring work with fewer manual steps.",
    examples: ["Multi-step processes", "Recurring workflows", "Planning jobs"]
  },
  {
    icon: "activity",
    title: "Monitoring & results",
    description: "Follow execution status, review recent runs, and ask for help understanding failures.",
    examples: ["Execution status", "Run evidence", "Failure guidance"]
  }
] as const;

export function Dashboard({ bootstrap, home, onStartAssistant }: DashboardProps) {
  const [prompt, setPrompt] = useState("");
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
          <p>Tell the AI Assistant what you want to accomplish. It can help identify the right Planning operation, collect missing details, prepare a governed action, and explain the result.</p>
          <div className="home-hero__actions">
            {assistantAvailable && <button className="button button--primary home-primary-action" type="button" onClick={() => onStartAssistant()}><Icon name="assistant" /> Start with AI Assistant <Icon name="arrow" /></button>}
            {myWorkAvailable && <a className="button button--quiet" href="#tasks">View My Work</a>}
          </div>
          <div className="home-trust-note"><Icon name="check" /><span><strong>You stay in control.</strong> Important actions can be reviewed or confirmed before execution.</span></div>
        </div>

        <AssistantIllustration />

        {assistantAvailable ? (
          <form className="home-assistant-entry" onSubmit={submitPrompt} aria-label="Start an AI Assistant request">
            <div className="home-assistant-entry__heading">
              <span><Icon name="assistant" /></span>
              <div><strong>Ask the AI Assistant</strong><small>Describe the outcome—not the menu or service.</small></div>
            </div>
            <div className="home-prompt-box">
              <label htmlFor="home-assistant-prompt">What would you like to accomplish in Planning?</label>
              <div>
                <input id="home-assistant-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="For example, run the Sales Forecast pipeline for FY27" />
                <button type="submit" aria-label="Continue this request in AI Assistant" disabled={!prompt.trim()}><Icon name="arrow" /></button>
              </div>
            </div>
            <div className="home-prompt-examples" aria-label="Example requests">
              <span>Try asking</span>
              <div>{examplePrompts.map((item) => <button type="button" key={item} onClick={() => choosePrompt(item)}>{item}</button>)}</div>
            </div>
          </form>
        ) : (
          <div className="home-assistant-unavailable"><Icon name="assistant" /><div><strong>Assistant access is not assigned to this role</strong><p>You can still use the Planning workspaces available in the navigation.</p></div></div>
        )}
      </section>

      <section className="home-section home-ai-explainer" aria-labelledby="ai-explainer-title">
        <SectionHeading eyebrow="Work with AI, not through menus" title="Tell the Assistant what you need." id="ai-explainer-title" description="You do not need to remember where a job, rule, integration, or Pipeline lives. Start with the business outcome and let the Assistant guide the supported path." />
        <div className="home-behavior-grid">
          <BehaviorCard number="01" icon="assistant" title="Understands your request" description="Describe the task naturally, such as “Load yesterday's actual data.”" />
          <BehaviorCard number="02" icon="search" title="Asks when details are missing" description="The Assistant clarifies important choices instead of guessing what should run." />
          <BehaviorCard number="03" icon="check" title="Helps complete the task" description="Review the prepared action, approve when required, and receive a clear result." />
        </div>
      </section>

      <section className="home-section" aria-labelledby="capabilities-title">
        <SectionHeading eyebrow="Planning capabilities" title="One place to start common Planning work" id="capabilities-title" description="Use the Assistant when you know the outcome, or open a dedicated workspace when you want direct control." />
        <div className="home-capability-grid">
          {capabilities.map((capability) => (
            <article className="home-capability-card" key={capability.title}>
              <span className="home-capability-card__icon"><Icon name={capability.icon} /></span>
              <h3>{capability.title}</h3>
              <p>{capability.description}</p>
              <ul>{capability.examples.map((example) => <li key={example}><Icon name="check" />{example}</li>)}</ul>
            </article>
          ))}
        </div>
      </section>

      <section className="home-section home-comparison" aria-labelledby="comparison-title">
        <SectionHeading eyebrow="A simpler way to work" title="From finding the right screen to describing the outcome" id="comparison-title" description="The underlying Oracle controls remain in place. The platform gives you a more convenient, guided way to reach them." />
        <div className="home-comparison__grid">
          <article className="home-manual-path">
            <span className="home-path-label">Traditional navigation</span>
            <h3>Several decisions before the work starts</h3>
            <ol>{["Find the correct screen", "Locate the artifact", "Configure parameters", "Run and monitor", "Interpret the result"].map((step, index) => <li key={step}><span>{index + 1}</span>{step}</li>)}</ol>
          </article>
          <article className="home-assisted-path">
            <header><span><Icon name="assistant" /></span><div><small>With the AI Assistant</small><strong>“Run the Sales Forecast pipeline for FY27.”</strong></div></header>
            <ol>
              <li><Icon name="search" /><div><strong>Identify</strong><small>Finds supported matches</small></div></li>
              <li><Icon name="assistant" /><div><strong>Clarify</strong><small>Asks only for missing details</small></div></li>
              <li><Icon name="check" /><div><strong>Confirm</strong><small>You review important actions</small></div></li>
              <li><Icon name="activity" /><div><strong>Complete</strong><small>Runs and returns the result</small></div></li>
            </ol>
          </article>
        </div>
      </section>

      <section className="home-section home-discovery" aria-labelledby="discovery-title">
        <div>
          <SectionHeading eyebrow="What can I ask?" title="Start with an everyday Planning request" id="discovery-title" description="These examples map to capabilities supported by the platform and connected Oracle environment." />
          <div className="home-ask-grid">
            <AskExample label="Load" prompt="Load actuals for September." onSelect={choosePrompt} disabled={!assistantAvailable} />
            <AskExample label="Calculate" prompt="Run the workforce calculation." onSelect={choosePrompt} disabled={!assistantAvailable} />
            <AskExample label="Automate" prompt="Run the month-end pipeline." onSelect={choosePrompt} disabled={!assistantAvailable} />
            <AskExample label="Monitor" prompt="What's the status of my data load?" onSelect={choosePrompt} disabled={!assistantAvailable} />
            <AskExample label="Troubleshoot" prompt="Why did the last metadata import fail?" onSelect={choosePrompt} disabled={!assistantAvailable} />
          </div>
        </div>
        {myWorkAvailable && <WorkPreview home={home} />}
      </section>
    </div>
  );
}

function AssistantIllustration() {
  return (
    <div className="home-assistant-illustration" aria-label="The AI Assistant turns a Planning request into a reviewed, monitored action">
      <div className="home-ai-orbit home-ai-orbit--one" />
      <div className="home-ai-orbit home-ai-orbit--two" />
      <article className="home-ai-request"><span>You</span><p>Run the Sales Forecast pipeline for FY27.</p></article>
      <div className="home-ai-core">
        <span className="home-ai-character" aria-hidden="true">
          <i className="home-ai-character__antenna" />
          <span className="home-ai-character__face"><i /><i /><b /></span>
        </span>
        <strong>Planning Assistant</strong><small>Understands · Clarifies · Prepares</small>
        <span className="home-ai-thinking" aria-hidden="true"><i /><i /><i /></span>
      </div>
      <article className="home-ai-result"><span><Icon name="check" /></span><div><strong>Ready for your review</strong><small>Pipeline and run context identified</small></div></article>
      <span className="home-ai-signal home-ai-signal--data"><Icon name="data" /></span>
      <span className="home-ai-signal home-ai-signal--automation"><Icon name="automation" /></span>
      <span className="home-ai-signal home-ai-signal--activity"><Icon name="activity" /></span>
    </div>
  );
}

function BehaviorCard({ number, icon, title, description }: { number: string; icon: "assistant" | "search" | "check"; title: string; description: string }) {
  return <article className="home-behavior-card"><span className="home-behavior-card__number">{number}</span><span className="home-behavior-card__icon"><Icon name={icon} /></span><h3>{title}</h3><p>{description}</p></article>;
}

function AskExample({ label, prompt, onSelect, disabled }: { label: string; prompt: string; onSelect: (prompt: string) => void; disabled: boolean }) {
  return <button className="home-ask-example" type="button" onClick={() => onSelect(prompt)} disabled={disabled}><span>{label}</span><strong>{prompt}</strong><Icon name="arrow" /></button>;
}

function WorkPreview({ home }: { home: HomeResponse }) {
  const attention = home.summary.action_required;
  const due = home.summary.due_today;
  return (
    <aside className="home-work-preview">
      <span className="home-work-preview__icon"><Icon name={attention ? "tasks" : "check"} /></span>
      <span className="eyebrow">Your work</span>
      <h2>{attention ? `${attention} item${attention === 1 ? " requires" : "s require"} attention` : "You're all caught up"}</h2>
      <p>{attention ? `${due ? `${due} due today. ` : ""}Open My Work to continue assignments, review dependencies, and see recent execution activity.` : "No Planning tasks currently require your attention."}</p>
      <a className="button button--quiet" href="#tasks">View My Work <Icon name="arrow" /></a>
    </aside>
  );
}

function SectionHeading({ eyebrow, title, description, id }: { eyebrow: string; title: string; description: string; id: string }) {
  return <header className="home-section-heading"><span className="eyebrow">{eyebrow}</span><h2 id={id}>{title}</h2><p>{description}</p></header>;
}

function greeting() { const hour = new Date().getHours(); return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"; }
function firstName(name: string) { return name.trim().split(/\s+/)[0] || name; }
