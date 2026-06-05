package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const memoriaBase = "http://localhost:19998"

type MemoriaClient struct {
	client *http.Client
}

type AgentInfo struct {
	ID              string   `json:"id"`
	Project         string   `json:"project"`
	Task            string   `json:"task"`
	Status          string   `json:"status"`
	Files           []string `json:"files"`
	CommitLog       []string `json:"commit_log"`
	StartedAt       float64  `json:"started_at"`
	LastHeartbeat   float64  `json:"last_heartbeat"`
	ConflictsWarned []string `json:"conflicts_warned"`
}

type TaskInfo struct {
	ID          string  `json:"id"`
	Project     string  `json:"project"`
	Title       string  `json:"title"`
	Status      string  `json:"status"`
	AssignedTo  string  `json:"assigned_to"`
	Result      string  `json:"result"`
	Error       string  `json:"error"`
	CreatedAt   float64 `json:"created_at"`
}

type MemoryEntry struct {
	Project string   `json:"project"`
	Entries []string `json:"entries"`
	Count   int      `json:"count"`
}

type RecallResult struct {
	Query   string         `json:"query"`
	Count   int            `json:"count"`
	Results []RecallHit    `json:"results"`
}

type RecallHit struct {
	ID      string `json:"id"`
	Title   string `json:"title"`
	Summary string `json:"summary"`
	Source  string `json:"source"`
	Room    string `json:"room,omitempty"`
	From    string `json:"from,omitempty"`
}

type HealthInfo struct {
	Ok              bool     `json:"ok"`
	SessionsIndexed int      `json:"sessions_indexed"`
	Topics          []string `json:"topics"`
	MemoriaVersion  string   `json:"memoria_version"`
	DBExists        bool     `json:"db_exists"`
}

func NewMemoriaClient() *MemoriaClient {
	return &MemoriaClient{
		client: &http.Client{Timeout: 10 * time.Second},
	}
}

func (m *MemoriaClient) get(path string, dest interface{}) error {
	resp, err := m.client.Get(memoriaBase + path)
	if err != nil {
		return fmt.Errorf("GET %s: %w", path, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if err := json.Unmarshal(body, dest); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

func (m *MemoriaClient) Health() (*HealthInfo, error) {
	var h HealthInfo
	if err := m.get("/health", &h); err != nil {
		return nil, err
	}
	return &h, nil
}

func (m *MemoriaClient) Agents() ([]AgentInfo, error) {
	var result struct {
		Agents []AgentInfo `json:"agents"`
		Count  int         `json:"count"`
	}
	if err := m.get("/agents", &result); err != nil {
		return nil, err
	}
	return result.Agents, nil
}

func (m *MemoriaClient) Tasks(project string) ([]TaskInfo, error) {
	path := "/tasks"
	if project != "" {
		path += "?project=" + project
	}
	var result struct {
		Tasks []TaskInfo `json:"tasks"`
		Count int        `json:"count"`
	}
	if err := m.get(path, &result); err != nil {
		return nil, err
	}
	return result.Tasks, nil
}

func (m *MemoriaClient) Memory(project string) (*MemoryEntry, error) {
	var result MemoryEntry
	if err := m.get("/memory/"+project, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

func (m *MemoriaClient) Recall(query string, limit int) ([]RecallHit, error) {
	q := strings.ReplaceAll(query, " ", "+")
	path := fmt.Sprintf("/recall?q=%s&limit=%d", q, limit)
	var result RecallResult
	if err := m.get(path, &result); err != nil {
		return nil, err
	}
	return result.Results, nil
}
