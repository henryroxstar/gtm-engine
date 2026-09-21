# gtm-engine (오픈소스)

<p align="center">
  <strong>언어 / Language:</strong>
  <a href="README.md">English</a> |
  <a href="README.zh-CN.md">简体中文</a> |
  <a href="README.ja.md">日本語</a> |
  <a href="README.es.md">Español</a> |
  <a href="README.de.md">Deutsch</a> |
  <strong>한국어</strong>
</p>

<p align="center">
  <a href="https://github.com/henryroxstar/gtm-engine/stargazers"><img src="https://img.shields.io/github/stars/henryroxstar/gtm-engine?style=flat&label=Stars" alt="Stars" /></a>
  <a href="https://twitter.com/intent/tweet?text=The%20open-source%20GTM%20agent%20harness%20for%20startups%3A%2063%20skills%2C%20zero%20auto-spam%2C%20runs%20locally%20in%20Claude%20Code%20or%20Antigravity.&url=https%3A%2F%2Fgithub.com%2Fhenryroxstar%2Fgtm-engine"><img src="https://img.shields.io/badge/Share%20on-X-black?style=flat&logo=x" alt="Share on X" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11+-3776AB.svg" alt="Python 3.11+" /></a>
  <a href="https://docs.anthropic.com/en/api/agent-sdk/overview"><img src="https://img.shields.io/badge/built%20with-Claude%20Agent%20SDK-d97757.svg" alt="Built with Claude Agent SDK" /></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/connectivity-MCP--first-6E56CF.svg" alt="MCP-first" /></a>
  <a href="#이러한-구조로-설계된-이유"><img src="https://img.shields.io/badge/human%20gates-%EC%9A%B0%ED%9A%8C%20%EB%B6%88%EA%B0%80%ED%95%9C%20%ED%9C%B4%EB%A8%BC%20%EA%B2%8C%EC%9D%B4%ED%8A%B8-2ea44f.svg" alt="Human Gates" /></a>
  <a href="#지원하는-ai-워크스페이스-및-환경"><img src="https://img.shields.io/badge/workspaces-Claude%20%7C%20Antigravity%20%7C%20Cursor%20%7C%20Codex-orange.svg" alt="Harness Support" /></a>
</p>

![GTM Content OS 및 아웃바운드 파이프라인](docs/assets/gtm-pipeline-flow.png)

*B2B 소프트웨어 스타트업을 위한 오픈소스 Go-To-Market (GTM) AI 에이전트 실행 환경. 초기 스타트업의 영업 개척, 프리세일즈, 필드 마케팅 역량을 10배로 확장합니다.*

```
      .-"""-.
     /  o o  \        하나의 두뇌, 신중한 여러 개의 손 ——
     \   ^   /        모든 발송과 게시는 당신의 직접 승인을 거칩니다
      )-----(
     / /| |\ \
    ( ( | | ) )
     \_/ | \_/
        `-`
```

### 당신은 창업자이자, 혼자서 GTM 팀 전체를 도맡고 있습니다.

스타트업 초기에는 리소스가 극도로 제한된 상태에서 프로덕트-마켓 핏(PMF)을 찾기 위해 분투해야 합니다. 고객 계약을 체결하는 것만이 전부는 아닙니다. 잠재 고객 파이프라인을 직접 구축하고, 고객의 생생한 목소리를 제품 개발진에게 전달해야 합니다. 아직 미완성이고 문서 업데이트가 멈춘 제품에 대해 매번 엔지니어를 동반하지 않고도 유창하게 설명할 수 있어야 하며, 매주 변화하는 시장 흐름을 읽고 신규 메시징을 테스트하여 적합한 구매자를 끌어들여야 합니다.

이것은 본래 5명의 전담 인력이 필요한 일입니다. 일반적인 조언은 "5명을 채용하라"고 하지만, 여러분에게 있는 것은 노트북 한 대, 기존 AI 워크스페이스 구독(Claude Desktop, Google Antigravity, Cursor, Codex), 그리고 주어진 이번 주 시간뿐입니다.

**gtm-engine은 이 5가지 역할을 당신과 함께 완수하는 전문 하네스입니다.** 타깃 고객 발굴(Prospecting), 미팅 사전 준비, 어카운트 플랜 수립, 제안 덱 제작, 주간 시장 레이더 탐지, 멀티 플랫폼 브랜드 콘텐츠 제작(LinkedIn 포스트, 기술 블로그, 팟캐스트 대본, 인포그래픽)까지 — 채팅창에 단 한 문장만 입력하면, 회사의 실제 지식베이스를 바탕으로 당신의 브랜드 보이스에 맞추어 생성됩니다.

무엇보다 **시스템 아키텍처상 에이전트가 단독으로 이메일을 자동 발송하거나, 글을 게시하거나, 데이터를 유출하는 것이 물리적으로 불가능하도록 설계**되어 있습니다. 막연한 신뢰를 요구하지 않고, 구조적으로 권한 남용을 차단합니다.

변하지 않는 3대 철칙:
1. **당신의 명시적 승인 없이는 단 하나의 메일도 발송되지 않고, 단 하나의 글도 게시되지 않습니다.**
2. **기업별 데이터는 각 사 전용 Profile(프로필) 디렉토리 내에 물리적으로 완벽히 격리됩니다.**
3. **에이전트는 통제되지 않은 원시 HTTP 호출이나 터미널 쉘 임의 실행 권한을 갖지 않습니다.** 모든 외부 접근은 검증된 MCP(Model Context Protocol) 툴을 통해서만 수행됩니다.

![Content OS Architecture](docs/assets/content-os-grade-a-plus-architecture.png)

---

### 왜 gtm-engine인가? (아키텍처 비교)

| 핵심 항목 | 블랙박스 "AI SDR" 상용 플랫폼 (11x, Artisan 등) | 단순 프롬프트 대화 (ChatGPT / Claude) | 일반 범용 에이전트 프레임워크 (CrewAI / LangChain) | **gtm-engine (본 시스템)** |
|---|---|---|---|---|
| **기본 비용** | 월 $500 – $3,000 | 월 $20 (반복적인 수동 복사/붙여넣기 수반) | 토큰 종량제 비용 + 서버 호스팅 비용 | **기본 $0** (보유 중인 기존 AI 워크스페이스 구독 활용) |
| **발송 안전성** | 콜드 메일 자동 발송 (도메인 평판 훼손 위험) | 수동 복사 및 검토 | 도구 권한의 광범위한 위임 | **우회 불가능한 휴먼 승인 게이트** (자동 발송 기능 원천 차단) |
| **회사 맥락 이해**| 기계적인 외부 웹 스크래핑 | 대화할 때마다 회사 소개 재입력 필요 | 복잡한 벡터 DB 및 파이프라인 구축 필요 | **Profile 세컨드 브레인** (한 번 설정으로 모든 스킬에 자동 반영) |
| **지원 워크플로우**| 콜드 이메일에 국한 | 텍스트 생성에 국한 | 복잡한 노드 그래프 코딩 필요 | **63개 전문 스킬 & 10대 패키지** (영상, 덱, 게시물, SDR) |
| **데이터 주권과 보안**| 서드파티 클라우드 벤더 종속 | 데이터가 모델 학습에 활용될 우려 | 사용자 구축 환경에 따라 상이함 | **100% 로컬 환경 / Gitignore** (데이터가 내 컴퓨터를 벗어나지 않음) |

---

### 30초 퀵스타트

```bash
# 1. 저장소 클론
git clone https://github.com/henryroxstar/gtm-engine.git && cd gtm-engine

# 2. 선호하는 AI 워크스페이스(Claude Desktop, Google Antigravity, Cursor, Codex)에서 이 폴더 열기

# 3. 대화창에 한 줄 입력:
"set me up" --site yourcompany.com
```
*첫 실행에는 Docker, 백그라운드 서버, 서드파티 API 키가 전혀 필요하지 않습니다.*

---

## 실제 동작 방식 (See it work)

설치 명령이나 사전문서 설정 없이 바로 동작합니다. 폴더를 열고 대화창에 한 문장만 입력해 보세요. 월요일 아침 "무언가 가치 있는 글을 올려야 하는데"라는 고민이 어떻게 완성되는지 확인할 수 있습니다:

![실제 동작 방식: 프롬프트에서 사람 승인까지](docs/assets/see-it-work-workflows.png)

1분 전까지만 해도 백지 상태였던 화면이, 철저한 리서치를 기반으로 내 생각과 톤을 충실히 반영한 완성도 높은 글로 채워집니다. 모든 문장은 내 손을 거쳐 검증됩니다.

이 동일한 대화형 인터페이스로 주간 주요 업무 전체를 수행할 수 있습니다:

---

## 어디서부터 시작할까요?

| 사용자 유형 및 목적 | 권장 진입 경로 | 소요 시간 |
|---|---|---|
| **비개발자** —— 코드보다는 비즈니스와 영업 성과에 집중하고 싶을 때 | [`END-USER-ONBOARDING.md`](END-USER-ONBOARDING.md) (터미널 및 명령어 불필요) | 약 30분 |
| **개발자 / 엔지니어** —— 워크스페이스 대화창 또는 터미널 중심 작업 | [시작하기 (Chat 모드)](#시작하기-chat-모드) | 약 10분 |
| **아키텍처 평가자** —— 보안 통제, 제어 흐름, 시스템 설계를 검토할 때 | [이러한 구조로 설계된 이유](#이러한-구조로-설계된-이유) | 약 10분 |

---

## 4가지 실행 및 연동 모드

gtm-engine는 하나의 공통 핵심 엔진(`gtm_core`)을 4가지 인터페이스로 제공합니다:

**1 · Chat 모드 (기본 권장 — 인프라 불필요)**
선호하는 AI 워크스페이스(**Claude Desktop**, **Google Antigravity**, **Cursor**, **Codex**)에서 본 폴더를 열고 `"set me up"`을 입력하세요. 모든 GTM 스킬이 로컬에서 실행되며, 내 프로필을 기반으로 내 목소리를 담아냅니다. VPS, Docker, 데이터베이스가 필요 없습니다. → [시작하기 (Chat 모드)](#시작하기-chat-모드)

**2 · 자율형 셀프 호스팅 Agent (24/7 자동 순항)**
**Claude Agent SDK** 기반 런타임을 로컬 백그라운드 또는 자체 **VPS**에 배포하여 뉴스 모니터링 → 기획 → 심층 조사 → 콘텐츠 작성 → 배포 준비를 24시간 자율 수행하며, Telegram 승인 게이트에서 사람의 지시를 기다립니다. 자세한 내용은 [`docs/DEPLOY.md`](docs/DEPLOY.md)를 참조하세요.

**3 · 클라이언트 REST API 개발**
FastAPI 로컬 백엔드(`./scripts/stack.sh start`, 포트 `:8000`)를 PostgreSQL 및 Redis와 함께 실행합니다. 커스텀 프론트엔드 UI, 대시보드 또는 모바일 클라이언트를 개발하는 엔지니어를 위해 제공됩니다.

**4 · 외부 연동용 GTM MCP 서버**
스트리밍 HTTP FastMCP(`deploy/Dockerfile.mcp`, 포트 `:8001`, API 키 인증 `sk-...`)로 선별된 GTM 도구를 노출하여 외부 에이전트(외부 Claude 인스턴스, LangChain, AutoGen, CrewAI 등)가 원격 도구 제공자로 연결할 수 있습니다.

### 실행 경로 선택 (혼선 방지)

| 경로 | 대상 사용자 | 실행 방법 | 하지 말아야 할 것 |
|---|---|---|---|
| **Chat 모드 (기본)** | 창업자, 영업 및 마케팅 실무자 | AI 워크스페이스에서 열고 `"set me up"` 입력 | **Docker 실행이나 `./scripts/stack.sh`를 실행할 필요가 없습니다.** |
| **셀프 호스팅 Agent** | 24/7 백그라운드 자동화를 원하는 팀 | [`docs/DEPLOY.md`](docs/DEPLOY.md)에 따라 Docker Compose 배포 | 실시간 대화를 기대하지 마세요. Telegram 승인 게이트로 동작합니다. |
| **클라이언트 API 개발** | 프론트엔드나 클라이언트를 개발하는 엔지니어 | `./scripts/stack.sh start` 실행 | 대화창에서 스킬을 쓰려는 목적이라면 실행하지 마세요. |
| **외부 연동 MCP 서버** | 외부 에이전트에서 GTM 도구를 원격 호출할 때 | 8001 포트에 FastMCP 컨테이너 배포 | API 키 인증 및 예산 상한 설정 없이 공개하지 마세요. |

### 지원하는 AI 워크스페이스 및 환경

| 워크스페이스 / 하네스 | 지원 수준 | 스킬 로드 방식 | 비고 |
|---|---|---|---|
| **Claude Desktop / Code** | 네이티브 지원 | 플러그인 시스템 (`plugin/`) | 63개 전체 스킬, MCP 도구, 대화형 승인 완벽 지원 |
| **Google Antigravity** | 네이티브 지원 | `.agents/` 자동 탐색 메커니즘 | 멀티 에이전트 협업, 네이티브 커맨드 및 파일 도구 매핑 |
| **Cursor / Codex** | 완벽 호환 | `.agents/AGENTS.md` + 룰셋 설정 | 대화형 인터랙션; 프롬프트 규칙에 따른 스킬 호출 |
| **Headless VPS (Agent SDK)**| 전용 런타임 | 컨테이너화된 에이전트 루프 | Telegram 봇 승인을 통한 24/7 자율 순항 실행 |

---

## 시작하기 (Chat 모드)

**사전 요구사항:** Python 3.11+ 및 [`uv`](https://docs.astral.sh/uv/). Chat 모드에서는 1단계에서 에이전트가 환경 구성을 친절하게 안내합니다.

**1단계 — 엔진 및 회사 프로필(Profile) 초기화**
채팅창에 `"set me up"`을 입력합니다. 환경 진단이 수행되며, 웹사이트 URL을 입력하면 회사 프로필(브랜드 아이덴티티, ICP, 경쟁사, 제품 정보)을 자동으로 추출하여 초기 세팅을 완료합니다.

**2단계 — 도구 및 API 키 설정 (선택 사항)**
모든 외부 데이터 연동은 선택 사항이며, 미설정 시 키가 필요 없는 공개 웹 검색으로 자동 대체됩니다:
- **잠재 고객 발굴 커넥터** (Vibe, RocketReach, Apollo): 검증된 기업 이메일 및 구매 의향 신호 수집.
- **웹 스크래핑 커넥터** (Firecrawl): 자바스크립트 렌더링 기반 동적 페이지 추출.
- **예산 지출 상한선 설정**: 월별 및 1회 실행별 지출 상한선을 설정하여 유료 API 호출 전 자동 검증을 진행합니다.

#### 환경 상태 진단 (`check_env`)
아래 진단 명령어로 프로필, 도구 연동 상태 및 예산 보호 설정이 올바른지 확인할 수 있습니다:
```bash
uv run python -m gtm_core.check_env
```

---

## 오늘 무엇을 하고 싶으신가요? (주요 작업 빠른 색인)

| 원하는 목표 | 채팅창에 입력할 문장 | 실행되는 주요 스킬 | 주요 산출물 |
|---|---|---|---|
| **타깃 잠재 고객 및 의사결정권자 발굴** | `"find prospects in [산업/시장]"` | `prospect`, `draft-outreach` | 점수화된 고객 브리프, CRM CSV, 검증된 연락처 |
| **중요한 영업 미팅 사전 준비** | `"prep me for my call with [회사명]"` | `call-prep`, `account-dossier` | 5분 핵심 브리프, SPIN 질문 세트, 고객 성공 사례 |
| **고품질 SNS 및 전문 칼럼 작성** | `"draft my LinkedIn post about [주제]"` | `content-radar`, `content-studio` | 3가지 훅 옵션 (Gate 1) $\rightarrow$ 형식 검증 완료 원고 (Gate 2) |
| **전문가 토론 및 커뮤니티 답변 작성** | `"reply to this post: [URL]"` | `linkedin-reply`, `reddit-reply` | 홍보성 없는 가치 중심 전문 답변안 (직접 검토) |
| **엔터프라이즈 맞춤형 기술 솔루션 설계** | `"design the solution for [회사명]"` | `solution-discovery`, `solution-design`| 공식 솔루션 아키텍처 문서 (SAD) 및 아키텍처 다이어그램 |
| **핵심 계정 공략 전략 수립** | `"build an account plan for [회사명]"` | `account-plan` | MEDDPICC 스코어카드, 구매 영향력 맵, 실행 로드맵 |
| **시스템 구동 환경 및 예산 점검** | `"run environment check"` | `check_env` CLI | 프로필 무결성, API 연동 상태, 예산 보호 종합 리포트 |

> 63개 전체 스킬 목록은 [`docs/SKILLS.md`](docs/SKILLS.md)를 참조하세요.

---

## 이러한 구조로 설계된 이유

1. **우회 불가능한 2단계 휴먼 게이트(Gate 1 / Gate 2)**
   기획 단계(Gate 1: 각도 및 훅 선택)와 최종 결과물 단계(Gate 2: 내용 최종 승인)에서 무조건 멈추도록 강제되어 있습니다. 전역적으로 `autopublish: false`가 설정되어 사람의 승인 없이 외부로 발송되는 일이 없습니다.
2. **멀티 테넌트 프로필 물리적 격리**
   각 회사의 데이터는 `profiles/<프로필명>/` 디렉토리에 개별 격리되어 한 엔진으로 여러 회사를 안전하게 전환하며 운영할 수 있습니다.
3. **모델은 두뇌, MCP 툴은 유일한 손발**
   에이전트는 원시 HTTP 요청을 직접 날리지 않으며, 인가된 MCP 도구만을 거쳐 외부와 상호작용합니다. 인증 키는 모델의 컨텍스트에 노출되지 않습니다.
4. **엔진 코드 내 하드코딩 배제**
   회사명이나 도메인 정보가 코드 내에 하드코딩되지 않고, 런타임 시 프로필에서 안전하게 주입됩니다.

---

## 스타 히스토리 (Star History)

[![Star History Chart](https://api.star-history.com/svg?repos=henryroxstar/gtm-engine&type=Date)](https://star-history.com/#henryroxstar/gtm-engine&Date)

---

## 라이선스

본 프로젝트는 **Apache License 2.0** 라이선스 하에 배포됩니다 —— 자세한 내용은 [`LICENSE`](LICENSE)를 참조하세요.
